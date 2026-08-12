"""Tests for the fix publish worker pipeline."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import (
    FixJob,
    FixJobStatus,
    FixPublishStatus,
    FixValidationStatus,
)
from app.models.repository import Repository, RepositoryPlatform
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.provider_installation_repository import (
    ProviderInstallationRepository,
)
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import FixIssueResult, FixIssueVerdict
from app.services import fix_publish_pipeline
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_publish_pipeline import FixPublishPipelineService
from app.services.git_provider.base import (
    ForkResult,
    GitProvider,
    InstallationAccessToken,
    PullRequestResult,
)


@pytest.mark.asyncio
async def test_publish_pipeline_publishes_fork_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job()
    repository = RecordingFixJobRepository(fix_job)
    audit_repository = RecordingAuditLogRepository()
    provider = FakeGitProvider(branch_head_sha=fix_job.base_commit_sha)
    patch_workspace_side_effects(monkeypatch)
    service = build_pipeline_service(repository, audit_repository, provider)

    await service.run(
        fix_job_id=fix_job.id,
        allow_failed_validation=False,
    )

    assert fix_job.status == FixJobStatus.APPROVED
    assert fix_job.publish_status == FixPublishStatus.PUBLISHED
    assert fix_job.pr_url == "https://github.com/example/repo/pull/1"
    assert audit_repository.actions == [
        FixAuditAction.BRANCH_PUSHED,
        FixAuditAction.PR_CREATED,
    ]


@pytest.mark.asyncio
async def test_publish_pipeline_marks_stale_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job()
    provider = FakeGitProvider(branch_head_sha="b" * 40)
    repository = RecordingFixJobRepository(fix_job)
    audit_repository = RecordingAuditLogRepository()
    patch_workspace_side_effects(monkeypatch)
    service = build_pipeline_service(repository, audit_repository, provider)

    await service.run(
        fix_job_id=fix_job.id,
        allow_failed_validation=False,
    )

    assert fix_job.publish_status == FixPublishStatus.STALE_BASE
    assert "Base branch changed" in (fix_job.publish_error or "")
    assert audit_repository.actions == [FixAuditAction.STALE_BASE]


@pytest.mark.asyncio
async def test_publish_pipeline_publishes_via_fork_for_different_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job()
    provider = FakeGitProvider(branch_head_sha=fix_job.base_commit_sha)
    repository = RecordingFixJobRepository(fix_job)
    audit_repository = RecordingAuditLogRepository()
    pushed_remotes: list[str] = []
    patch_workspace_side_effects(monkeypatch, pushed_remotes=pushed_remotes)
    service = build_pipeline_service(repository, audit_repository, provider)

    await service.run(
        fix_job_id=fix_job.id,
        allow_failed_validation=False,
    )

    assert fix_job.publish_status == FixPublishStatus.PUBLISHED
    assert fix_job.fork_repository_full_name == "example-user/repo"
    assert provider.fork_requests == [("example/repo", "example-user")]
    assert provider.pull_request_heads == [f"example-user:{fix_job.fix_branch}"]
    assert pushed_remotes == ["fork"]
    assert audit_repository.actions == [
        FixAuditAction.BRANCH_PUSHED,
        FixAuditAction.PR_CREATED,
    ]


@pytest.mark.asyncio
async def test_publish_pipeline_publishes_directly_for_same_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job()
    provider = FakeGitProvider(branch_head_sha=fix_job.base_commit_sha)
    repository = RecordingFixJobRepository(fix_job)
    audit_repository = RecordingAuditLogRepository()
    pushed_remotes: list[str] = []
    patch_workspace_side_effects(monkeypatch, pushed_remotes=pushed_remotes)
    service = build_pipeline_service(
        repository,
        audit_repository,
        provider,
        account_login="example",
    )

    await service.run(
        fix_job_id=fix_job.id,
        allow_failed_validation=False,
    )

    assert fix_job.publish_status == FixPublishStatus.PUBLISHED
    assert fix_job.fork_repository_full_name is None
    assert provider.fork_requests == []
    assert provider.pull_request_heads == [fix_job.fix_branch]
    assert pushed_remotes == ["origin"]
    assert audit_repository.actions == [
        FixAuditAction.BRANCH_PUSHED,
        FixAuditAction.PR_CREATED,
    ]


@pytest.mark.asyncio
async def test_pull_request_body_labels_unverified_override() -> None:
    fix_job = build_fix_job()
    issue_id = UUID(fix_job.issue_ids[0])
    fix_job.validation_status = FixValidationStatus.FAILED
    fix_job.validation_output = {
        "status": FixValidationStatus.FAILED.value,
        "summary": "Validation failed.",
        "checks": [
            {
                "name": "ruff check",
                "command": "ruff check src/app.py",
                "kind": "lint",
                "required": True,
                "status": "failed",
                "exit_code": 1,
                "stdout": "",
                "stderr": "F821 undefined name",
                "duration_ms": 1,
            }
        ],
    }
    fix_job.publish_allow_failed_validation = True
    fix_job.publish_override_reason = (
        "Release owner accepted the remaining manual verification risk."
    )
    fix_job.issue_results = [
        FixIssueResult(
            issue_id=issue_id,
            verdict=FixIssueVerdict.UNCERTAIN,
            summary="Dependency lockfile was unavailable.",
        ).model_dump(mode="json")
    ]
    service = build_pipeline_service(
        RecordingFixJobRepository(fix_job),
        RecordingAuditLogRepository(),
        FakeGitProvider(branch_head_sha=fix_job.base_commit_sha),
    )

    body = await service._build_pull_request_body(fix_job)

    assert "Unverified manual override" in body
    assert fix_job.publish_override_reason in body
    assert str(issue_id) in body
    assert "ruff check" in body
    assert "F821 undefined name" in body


def test_publish_worker_rejects_override_without_persisted_reason() -> None:
    fix_job = build_fix_job()
    fix_job.validation_status = FixValidationStatus.FAILED
    fix_job.publish_allow_failed_validation = True
    service = build_pipeline_service(
        RecordingFixJobRepository(fix_job),
        RecordingAuditLogRepository(),
        FakeGitProvider(branch_head_sha=fix_job.base_commit_sha),
    )

    with pytest.raises(FixPipelineError, match="reason was not persisted"):
        service._ensure_worker_preconditions(
            fix_job,
            allow_failed_validation=True,
        )


def test_publish_worker_rejects_queue_override_mismatch() -> None:
    fix_job = build_fix_job()
    service = build_pipeline_service(
        RecordingFixJobRepository(fix_job),
        RecordingAuditLogRepository(),
        FakeGitProvider(branch_head_sha=fix_job.base_commit_sha),
    )

    with pytest.raises(FixPipelineError, match="persisted user approval"):
        service._ensure_worker_preconditions(
            fix_job,
            allow_failed_validation=True,
        )


def build_pipeline_service(
    repository: "RecordingFixJobRepository",
    audit_repository: "RecordingAuditLogRepository",
    provider: "FakeGitProvider",
    *,
    account_login: str = "example-user",
) -> FixPublishPipelineService:
    return FixPublishPipelineService(
        settings=cast(
            Settings,
            SimpleNamespace(
                sandbox_root=".sandbox",
                max_repo_size_mb=500,
                frontend_base_url="http://localhost:3000",
            ),
        ),
        fix_job_repository=cast(FixJobRepository, repository),
        provider_installation_repository=cast(
            ProviderInstallationRepository,
            RecordingProviderInstallationRepository(account_login=account_login),
        ),
        audit_log_repository=cast(FixAuditLogRepository, audit_repository),
        report_repository=cast(ReportRepository, RecordingReportRepository()),
        github_provider=cast(GitProvider, provider),
    )


def build_fix_job() -> FixJob:
    user_id = uuid4()
    repository = Repository(
        id=uuid4(),
        user_id=user_id,
        name="repo",
        url="https://github.com/example/repo.git",
        platform=RepositoryPlatform.GITHUB,
        default_branch="main",
    )
    review_job = ReviewJob(
        id=uuid4(),
        repository_id=repository.id,
        user_id=user_id,
        status=ReviewJobStatus.COMPLETED,
        branch="main",
        commit_sha="a" * 40,
        repository=repository,
    )
    return FixJob(
        id=uuid4(),
        review_job_id=review_job.id,
        review_job=review_job,
        user_id=user_id,
        status=FixJobStatus.WAITING_APPROVAL,
        validation_status=FixValidationStatus.PASSED,
        issue_ids=[str(uuid4())],
        target_branch="main",
        base_commit_sha="a" * 40,
        fix_branch=f"repoguard/fix/{uuid4()}",
        diff="diff --git a/src/app.py b/src/app.py",
        changed_files=["src/app.py"],
        validation_output={
            "status": FixValidationStatus.PASSED.value,
            "summary": "Validation passed.",
            "checks": [],
        },
        publish_status=FixPublishStatus.PUBLISHING,
        publish_strategy="fork",
        publish_allow_failed_validation=False,
        publish_override_reason=None,
        created_at=datetime.now(UTC),
    )


def patch_workspace_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pushed_remotes: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        fix_publish_pipeline,
        "prepare_fix_workspace",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        fix_publish_pipeline, "validate_repo_size", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        fix_publish_pipeline,
        "apply_git_diff",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        fix_publish_pipeline,
        "create_git_commit",
        lambda **_kwargs: "c" * 40,
    )
    monkeypatch.setattr(
        fix_publish_pipeline,
        "set_git_remote_url",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        fix_publish_pipeline,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    def push_git_branch(*, remote_name: str, **_kwargs: object) -> None:
        if pushed_remotes is not None:
            pushed_remotes.append(remote_name)

    monkeypatch.setattr(fix_publish_pipeline, "push_git_branch", push_git_branch)


async def noop_publish_progress(*_args: object, **_kwargs: object) -> int:
    return 0


class FakeGitProvider:
    def __init__(self, *, branch_head_sha: str) -> None:
        self.branch_head_sha = branch_head_sha
        self.fork_requests: list[tuple[str, str]] = []
        self.pull_request_heads: list[str] = []

    async def create_installation_access_token(
        self,
        _installation_id: str,
    ) -> InstallationAccessToken:
        return InstallationAccessToken(token="token", expires_at=None)

    async def get_branch_head_sha(
        self,
        *,
        repository_full_name: str,
        branch: str,
        token: str,
    ) -> str:
        _ = repository_full_name, branch, token
        return self.branch_head_sha

    async def create_pull_request(
        self,
        *,
        repository_full_name: str,
        head: str,
        base: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequestResult:
        _ = repository_full_name, head, base, title, body, token
        self.pull_request_heads.append(head)
        return PullRequestResult(url="https://github.com/example/repo/pull/1")

    async def create_or_get_fork(
        self,
        *,
        repository_full_name: str,
        fork_owner: str,
        token: str,
    ) -> ForkResult:
        _ = repository_full_name, token
        self.fork_requests.append((repository_full_name, fork_owner))
        return ForkResult(
            full_name=f"{fork_owner}/repo",
            clone_url=f"https://github.com/{fork_owner}/repo.git",
        )


class RecordingFixJobRepository:
    def __init__(self, fix_job: FixJob) -> None:
        self.fix_job = fix_job

    async def get_by_id(self, _fix_job_id: UUID) -> FixJob | None:
        return self.fix_job

    async def mark_publish_started(
        self,
        fix_job: FixJob,
        *,
        sandbox_path: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_sandbox_path = sandbox_path
        return fix_job

    async def mark_publish_succeeded(
        self,
        fix_job: FixJob,
        *,
        provider: RepositoryPlatform,
        pr_url: str,
        published_branch: str,
        published_commit_sha: str,
        fork_repository_full_name: str | None,
        fork_branch: str | None,
        upstream_repository_full_name: str | None,
    ) -> FixJob:
        fix_job.status = FixJobStatus.APPROVED
        fix_job.publish_status = FixPublishStatus.PUBLISHED
        fix_job.provider = provider
        fix_job.pr_url = pr_url
        fix_job.published_branch = published_branch
        fix_job.published_commit_sha = published_commit_sha
        fix_job.fork_repository_full_name = fork_repository_full_name
        fix_job.fork_branch = fork_branch
        fix_job.upstream_repository_full_name = upstream_repository_full_name
        return fix_job

    async def mark_publish_needs_fork(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.NEEDS_FORK
        fix_job.publish_error = error_message
        return fix_job

    async def mark_publish_stale_base(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.STALE_BASE
        fix_job.publish_error = error_message
        return fix_job

    async def mark_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.FAILED
        fix_job.publish_error = error_message
        return fix_job

    async def get_publish_status(self, _fix_job_id: UUID) -> FixPublishStatus | None:
        return self.fix_job.publish_status

    async def rollback(self) -> None:
        return None


class RecordingAuditLogRepository:
    def __init__(self) -> None:
        self.actions: list[FixAuditAction] = []

    async def append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        _ = fix_job_id, user_id, message, event_metadata
        self.actions.append(action)


class RecordingProviderInstallationRepository:
    def __init__(self, *, account_login: str) -> None:
        self.account_login = account_login

    async def get_primary_for_user_provider(
        self,
        *,
        user_id: UUID,
        provider: RepositoryPlatform,
    ) -> SimpleNamespace:
        _ = user_id, provider
        return SimpleNamespace(
            installation_id="123",
            account_login=self.account_login,
        )


class RecordingReportRepository:
    async def list_issues_by_ids(
        self,
        *,
        job_id: UUID,
        issue_ids: list[UUID],
    ) -> list[object]:
        _ = job_id, issue_ids
        return []
