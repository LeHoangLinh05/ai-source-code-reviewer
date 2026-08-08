"""Worker pipeline for publishing generated fixes as pull requests."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.config import Settings
from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import (
    FixJob,
    FixJobStatus,
    FixPublishStatus,
    FixValidationStatus,
)
from app.models.repository import RepositoryPlatform
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.provider_installation_repository import (
    ProviderInstallationRepository,
)
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import normalize_fix_validation_result
from app.services.fix_notification_service import publish_fix_job_progress
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_pipeline.workspace import (
    apply_git_diff,
    create_git_commit,
    prepare_fix_workspace,
    push_git_branch,
    set_git_remote_url,
)
from app.services.git_provider.base import (
    GitProvider,
    GitProviderError,
    GitProviderPermissionError,
)
from app.services.git_provider.github import (
    build_authenticated_github_url,
    parse_github_repository_full_name,
)
from app.services.review_pipeline.workspace import validate_repo_size

logger = logging.getLogger(__name__)

PUBLISH_SANDBOX_DIR = "fix-publishes"
ORIGIN_REMOTE_NAME = "origin"
FORK_REMOTE_NAME = "fork"
GITHUB_PROVIDER_REQUIRED_MESSAGE = (
    "GitHub App connection is required before publishing pull requests."
)
GITHUB_ONLY_PUBLISH_MESSAGE = "Only GitHub publishing is supported in this version."
STALE_BASE_MESSAGE_TEMPLATE = (
    "Base branch changed: reviewed {base_sha} but {target_branch} is now {head_sha}."
)
PUBLISH_COMMIT_MESSAGE_TEMPLATE = "RepoGuard fix {fix_job_id}"


class FixPublishCanceledError(RuntimeError):
    """Raised when a user cancels an in-flight publish before side effects."""


@dataclass(slots=True)
class PublishOutcome:
    """Successful publish metadata."""

    pr_url: str
    published_branch: str
    published_commit_sha: str
    fork_repository_full_name: str | None
    fork_branch: str | None
    upstream_repository_full_name: str | None


class FixPublishPipelineService:
    """Orchestrate clone, diff apply, branch push, and PR creation."""

    def __init__(
        self,
        *,
        settings: Settings,
        fix_job_repository: FixJobRepository,
        provider_installation_repository: ProviderInstallationRepository,
        audit_log_repository: FixAuditLogRepository,
        report_repository: ReportRepository,
        github_provider: GitProvider,
    ) -> None:
        self.settings = settings
        self.fix_job_repository = fix_job_repository
        self.provider_installation_repository = provider_installation_repository
        self.audit_log_repository = audit_log_repository
        self.report_repository = report_repository
        self.github_provider = github_provider

    async def run(
        self,
        *,
        fix_job_id: UUID,
        allow_failed_validation: bool,
    ) -> None:
        """Run one publish attempt for a generated fix job."""

        started_at = time.perf_counter()
        fix_job = await self.fix_job_repository.get_by_id(fix_job_id)
        if fix_job is None:
            logger.info("Fix publish %s no longer exists; skipping", fix_job_id)
            return
        if fix_job.pr_url or fix_job.publish_status == FixPublishStatus.PUBLISHED:
            logger.info("Fix publish %s already has a PR; skipping", fix_job_id)
            return
        if fix_job.publish_status != FixPublishStatus.PUBLISHING:
            logger.info("Fix publish %s is not active; skipping", fix_job_id)
            return

        sandbox_path = (
            Path(self.settings.sandbox_root) / PUBLISH_SANDBOX_DIR / str(fix_job_id)
        )
        try:
            fix_job = await self.fix_job_repository.mark_publish_started(
                fix_job,
                sandbox_path=str(sandbox_path),
            )
            await publish_fix_job_progress(
                fix_job,
                message="Preparing publish sandbox.",
            )
            await self._run_publish_steps(
                fix_job=fix_job,
                sandbox_path=sandbox_path,
                allow_failed_validation=allow_failed_validation,
            )
            elapsed_seconds = time.perf_counter() - started_at
            logger.info("Fix publish %s finished in %.2fs", fix_job_id, elapsed_seconds)
        except FixPublishCanceledError:
            logger.info("Fix publish %s was canceled before completion", fix_job_id)
        except Exception as error:
            await self._handle_failure(fix_job_id, error)

    async def _run_publish_steps(
        self,
        *,
        fix_job: FixJob,
        sandbox_path: Path,
        allow_failed_validation: bool,
    ) -> None:
        self._ensure_worker_preconditions(
            fix_job,
            allow_failed_validation=allow_failed_validation,
        )
        source_repository = fix_job.review_job.repository
        if source_repository is None:
            raise FixPipelineError("Source repository is unavailable")
        if source_repository.platform != RepositoryPlatform.GITHUB:
            raise FixPipelineError(GITHUB_ONLY_PUBLISH_MESSAGE)

        installation = (
            await self.provider_installation_repository.get_primary_for_user_provider(
                user_id=fix_job.user_id,
                provider=RepositoryPlatform.GITHUB,
            )
        )
        if installation is None:
            raise FixPipelineError(GITHUB_PROVIDER_REQUIRED_MESSAGE)

        access_token = await self.github_provider.create_installation_access_token(
            installation.installation_id,
        )
        repository_full_name = parse_github_repository_full_name(source_repository.url)
        remote_head_sha = await self.github_provider.get_branch_head_sha(
            repository_full_name=repository_full_name,
            branch=fix_job.target_branch,
            token=access_token.token,
        )
        if remote_head_sha != fix_job.base_commit_sha:
            await self._mark_stale_base(
                fix_job,
                head_sha=remote_head_sha,
            )
            return

        clone_url = build_authenticated_github_url(
            source_repository.url,
            access_token.token,
        )
        prepare_fix_workspace(
            repository_url=source_repository.url,
            clone_url=clone_url,
            target_branch=fix_job.target_branch,
            base_commit_sha=fix_job.base_commit_sha,
            fix_branch=fix_job.fix_branch,
            sandbox_path=sandbox_path,
            sandbox_root=Path(self.settings.sandbox_root),
        )
        validate_repo_size(
            sandbox_path,
            max_size_bytes=self.settings.max_repo_size_mb * 1024 * 1024,
        )
        apply_git_diff(sandbox_path=sandbox_path, diff=fix_job.diff or "")
        commit_sha = create_git_commit(
            sandbox_path=sandbox_path,
            message=PUBLISH_COMMIT_MESSAGE_TEMPLATE.format(fix_job_id=fix_job.id),
        )
        await self._ensure_not_canceled(fix_job.id)

        repository_owner = _get_repository_owner(repository_full_name)
        if repository_owner.casefold() == installation.account_login.casefold():
            outcome = await self._publish_to_origin(
                fix_job=fix_job,
                sandbox_path=sandbox_path,
                repository_full_name=repository_full_name,
                repository_url=source_repository.url,
                token=access_token.token,
                commit_sha=commit_sha,
            )
        else:
            outcome = await self._publish_via_fork(
                fix_job=fix_job,
                sandbox_path=sandbox_path,
                repository_full_name=repository_full_name,
                fork_owner=installation.account_login,
                token=access_token.token,
                commit_sha=commit_sha,
            )

        updated_job = await self.fix_job_repository.mark_publish_succeeded(
            fix_job,
            provider=RepositoryPlatform.GITHUB,
            pr_url=outcome.pr_url,
            published_branch=outcome.published_branch,
            published_commit_sha=outcome.published_commit_sha,
            fork_repository_full_name=outcome.fork_repository_full_name,
            fork_branch=outcome.fork_branch,
            upstream_repository_full_name=outcome.upstream_repository_full_name,
        )
        await publish_fix_job_progress(
            updated_job,
            message="Pull request published.",
        )

    async def _publish_via_fork(
        self,
        *,
        fix_job: FixJob,
        sandbox_path: Path,
        repository_full_name: str,
        fork_owner: str,
        token: str,
        commit_sha: str,
    ) -> PublishOutcome:
        fork = await self.github_provider.create_or_get_fork(
            repository_full_name=repository_full_name,
            fork_owner=fork_owner,
            token=token,
        )
        fork_url = build_authenticated_github_url(fork.clone_url, token)
        set_git_remote_url(
            sandbox_path=sandbox_path,
            remote_name=FORK_REMOTE_NAME,
            remote_url=fork_url,
        )
        push_git_branch(
            sandbox_path=sandbox_path,
            branch_name=fix_job.fix_branch,
            remote_name=FORK_REMOTE_NAME,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job.id,
            user_id=None,
            action=FixAuditAction.BRANCH_PUSHED,
            message="Fix branch pushed to fork repository.",
            event_metadata={
                "branch": fix_job.fix_branch,
                "commit_sha": commit_sha,
                "fork_repository_full_name": fork.full_name,
            },
        )
        await self._ensure_not_canceled(fix_job.id)
        fork_head = f"{fork.full_name.split('/', maxsplit=1)[0]}:{fix_job.fix_branch}"
        pr_body = await self._build_pull_request_body(fix_job)
        pull_request = await self.github_provider.create_pull_request(
            repository_full_name=repository_full_name,
            head=fork_head,
            base=fix_job.target_branch,
            title=_build_pull_request_title(fix_job),
            body=pr_body,
            token=token,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job.id,
            user_id=None,
            action=FixAuditAction.PR_CREATED,
            message="Pull request created from fork repository.",
            event_metadata={
                "pr_url": pull_request.url,
                "fork_repository_full_name": fork.full_name,
            },
        )
        return PublishOutcome(
            pr_url=pull_request.url,
            published_branch=fix_job.fix_branch,
            published_commit_sha=commit_sha,
            fork_repository_full_name=fork.full_name,
            fork_branch=fix_job.fix_branch,
            upstream_repository_full_name=repository_full_name,
        )

    async def _publish_to_origin(
        self,
        *,
        fix_job: FixJob,
        sandbox_path: Path,
        repository_full_name: str,
        repository_url: str,
        token: str,
        commit_sha: str,
    ) -> PublishOutcome:
        origin_url = build_authenticated_github_url(repository_url, token)
        set_git_remote_url(
            sandbox_path=sandbox_path,
            remote_name=ORIGIN_REMOTE_NAME,
            remote_url=origin_url,
        )
        push_git_branch(
            sandbox_path=sandbox_path,
            branch_name=fix_job.fix_branch,
            remote_name=ORIGIN_REMOTE_NAME,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job.id,
            user_id=None,
            action=FixAuditAction.BRANCH_PUSHED,
            message="Fix branch pushed to source repository.",
            event_metadata={
                "branch": fix_job.fix_branch,
                "commit_sha": commit_sha,
                "repository_full_name": repository_full_name,
            },
        )
        await self._ensure_not_canceled(fix_job.id)
        pr_body = await self._build_pull_request_body(fix_job)
        pull_request = await self.github_provider.create_pull_request(
            repository_full_name=repository_full_name,
            head=fix_job.fix_branch,
            base=fix_job.target_branch,
            title=_build_pull_request_title(fix_job),
            body=pr_body,
            token=token,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job.id,
            user_id=None,
            action=FixAuditAction.PR_CREATED,
            message="Pull request created from source repository.",
            event_metadata={
                "pr_url": pull_request.url,
                "repository_full_name": repository_full_name,
            },
        )
        return PublishOutcome(
            pr_url=pull_request.url,
            published_branch=fix_job.fix_branch,
            published_commit_sha=commit_sha,
            fork_repository_full_name=None,
            fork_branch=None,
            upstream_repository_full_name=repository_full_name,
        )

    async def _mark_stale_base(
        self,
        fix_job: FixJob,
        *,
        head_sha: str,
    ) -> None:
        error_message = STALE_BASE_MESSAGE_TEMPLATE.format(
            base_sha=fix_job.base_commit_sha,
            target_branch=fix_job.target_branch,
            head_sha=head_sha,
        )
        updated_job = await self.fix_job_repository.mark_publish_stale_base(
            fix_job,
            error_message=error_message,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job.id,
            user_id=None,
            action=FixAuditAction.STALE_BASE,
            message=error_message,
            event_metadata={
                "base_commit_sha": fix_job.base_commit_sha,
                "remote_head_sha": head_sha,
                "target_branch": fix_job.target_branch,
            },
        )
        await publish_fix_job_progress(updated_job, message=error_message)

    async def _build_pull_request_body(self, fix_job: FixJob) -> str:
        issue_ids = [UUID(issue_id) for issue_id in fix_job.issue_ids]
        issues = await self.report_repository.list_issues_by_ids(
            job_id=fix_job.review_job_id,
            issue_ids=issue_ids,
        )
        issue_lines = [
            f"- `{issue.id}` — {issue.title} (`{issue.file_path}`)" for issue in issues
        ] or [f"- `{issue_id}`" for issue_id in fix_job.issue_ids]
        validation_summary = normalize_fix_validation_result(
            validation_status=fix_job.validation_status,
            validation_output=fix_job.validation_output,
        )
        report_link = _build_review_report_link(
            frontend_base_url=self.settings.frontend_base_url,
            review_job_id=fix_job.review_job_id,
        )
        validation_text = (
            validation_summary.summary
            if validation_summary is not None
            else "Validation output was unavailable."
        )
        issue_text = "\n".join(issue_lines)
        return (
            "## RepoGuard AI Fix\n\n"
            "This PR was generated from a user-approved RepoGuard fix job.\n\n"
            "### Selected issues\n"
            f"{issue_text}\n\n"
            "### Review context\n"
            f"- Base commit: `{fix_job.base_commit_sha}`\n"
            f"- Target branch: `{fix_job.target_branch}`\n"
            f"- Review report: {report_link}\n\n"
            "### Validation\n"
            f"{validation_text}\n"
        )

    async def _ensure_not_canceled(self, fix_job_id: UUID) -> None:
        publish_status = await self.fix_job_repository.get_publish_status(fix_job_id)
        if publish_status != FixPublishStatus.PUBLISHING:
            raise FixPublishCanceledError

    def _ensure_worker_preconditions(
        self,
        fix_job: FixJob,
        *,
        allow_failed_validation: bool,
    ) -> None:
        if fix_job.status != FixJobStatus.WAITING_APPROVAL:
            raise FixPipelineError("Fix job is not waiting for approval")
        if not fix_job.diff or not fix_job.changed_files:
            raise FixPipelineError("Fix job does not have a generated diff")
        if fix_job.validation_output is None:
            raise FixPipelineError("Fix job does not have validation results")
        if (
            fix_job.validation_status == FixValidationStatus.FAILED
            and not allow_failed_validation
        ):
            raise FixPipelineError("Validation failed and publish override was not set")

    async def _handle_failure(self, fix_job_id: UUID, error: Exception) -> None:
        error_message = _build_publish_error_message(error)
        logger.exception("Fix publish %s failed: %s", fix_job_id, error_message)
        await self.fix_job_repository.rollback()
        fix_job = await self.fix_job_repository.get_by_id(fix_job_id)
        if fix_job is None:
            logger.info(
                "Fix publish %s was removed before failure could persist",
                fix_job_id,
            )
            return

        updated_job = await self.fix_job_repository.mark_publish_failed(
            fix_job,
            error_message=error_message,
        )
        await self.audit_log_repository.append(
            fix_job_id=fix_job_id,
            user_id=None,
            action=FixAuditAction.PUBLISH_FAILED,
            message=error_message,
        )
        await publish_fix_job_progress(
            updated_job,
            message=error_message,
        )


def _build_publish_error_message(error: Exception) -> str:
    if isinstance(error, GitProviderPermissionError):
        return f"GitHub permission denied: {error}"
    if isinstance(error, (FixPipelineError, GitProviderError)):
        return str(error)

    return f"Fix publish failed: {error}"


def _build_pull_request_title(fix_job: FixJob) -> str:
    issue_count = len(fix_job.issue_ids)
    suffix = "issue" if issue_count == 1 else "issues"
    return f"RepoGuard fix for {issue_count} {suffix}"


def _build_review_report_link(
    *,
    frontend_base_url: str | None,
    review_job_id: UUID,
) -> str:
    if frontend_base_url is None:
        return f"RepoGuard review job `{review_job_id}`"

    return f"{frontend_base_url.rstrip('/')}/reviews/{review_job_id}"


def _get_repository_owner(repository_full_name: str) -> str:
    return repository_full_name.split("/", maxsplit=1)[0]
