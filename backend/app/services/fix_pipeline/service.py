"""Worker pipeline for generated code-fix patches."""

import logging
import time
from pathlib import Path
from typing import cast
from uuid import UUID

from app.core.config import Settings
from app.models.fix_job import FixJob, FixJobStatus, FixValidationStatus
from app.models.review_issue import ReviewIssue
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import FixValidationResult
from app.services.fix_notification_service import publish_fix_job_progress
from app.services.fix_pipeline.errors import (
    FixPipelineError,
    build_fix_error_message,
)
from app.services.fix_pipeline.generation import (
    generate_fix_changes,
    repair_validation_failures,
)
from app.services.fix_pipeline.validation import validate_fix
from app.services.fix_pipeline.workspace import (
    get_changed_files,
    get_git_diff,
    prepare_fix_workspace,
)
from app.services.review_pipeline.workspace import validate_repo_size

logger = logging.getLogger(__name__)

MAX_VALIDATION_REPAIR_ATTEMPTS = 2


class FixPipelineService:
    """Orchestrate clone, patch generation, validation, and persistence."""

    def __init__(
        self,
        *,
        settings: Settings,
        fix_job_repository: FixJobRepository,
        report_repository: ReportRepository,
    ) -> None:
        self.settings = settings
        self.fix_job_repository = fix_job_repository
        self.report_repository = report_repository

    async def run(self, fix_job_id: UUID) -> None:
        """Run the full patch-only fix pipeline for one job."""

        started_at = time.perf_counter()
        sandbox_path = Path(self.settings.sandbox_root) / "fixes" / str(fix_job_id)
        sandbox_root = Path(self.settings.sandbox_root)
        fix_job = await self.fix_job_repository.get_by_id(fix_job_id)
        if fix_job is None:
            logger.info("Fix job %s no longer exists; skipping worker run", fix_job_id)
            return

        try:
            fix_job = await self.fix_job_repository.mark_started(
                fix_job,
                sandbox_path=str(sandbox_path),
            )
            await publish_fix_job_progress(fix_job)
            await self._run_pipeline_steps(fix_job, sandbox_path, sandbox_root)
            elapsed_seconds = time.perf_counter() - started_at
            logger.info("Fix job %s prepared in %.2fs", fix_job_id, elapsed_seconds)
        except Exception as error:
            await self._handle_failure(fix_job_id, error)

    async def _run_pipeline_steps(
        self,
        fix_job: FixJob,
        sandbox_path: Path,
        sandbox_root: Path,
    ) -> None:
        review_job = fix_job.review_job
        source_repository = review_job.repository
        if source_repository is None:
            raise FixPipelineError("Source repository is unavailable")

        prepare_fix_workspace(
            repository_url=source_repository.url,
            target_branch=fix_job.target_branch,
            base_commit_sha=fix_job.base_commit_sha,
            fix_branch=fix_job.fix_branch,
            sandbox_path=sandbox_path,
            sandbox_root=sandbox_root,
        )
        validate_repo_size(
            sandbox_path,
            max_size_bytes=self.settings.max_repo_size_mb * 1024 * 1024,
        )

        fix_job = await self._transition(fix_job, FixJobStatus.GENERATING_PATCH)
        issues = await self._load_selected_issues(fix_job)
        await generate_fix_changes(
            sandbox_path=sandbox_path,
            issues=issues,
            timeout_seconds=self.settings.analysis_subprocess_timeout_seconds,
        )
        diff = get_git_diff(sandbox_path)
        if not diff.strip():
            raise FixPipelineError("Fix generation produced no code changes")

        changed_files = get_changed_files(sandbox_path)
        fix_job = await self.fix_job_repository.save_patch(
            fix_job,
            diff=diff,
            changed_files=changed_files,
        )

        fix_job = await self._transition(
            fix_job,
            FixJobStatus.VALIDATING,
            data={"changed_files": changed_files},
        )
        validation_result, changed_files = await self._validate_and_repair_patch(
            fix_job=fix_job,
            sandbox_path=sandbox_path,
            changed_files=changed_files,
        )
        diff = get_git_diff(sandbox_path)
        if not diff.strip():
            raise FixPipelineError("Validation repair removed all code changes")

        fix_job = await self.fix_job_repository.save_patch(
            fix_job,
            diff=diff,
            changed_files=changed_files,
        )
        validation_payload = cast(
            dict[str, object],
            validation_result.model_dump(mode="json"),
        )
        fix_job = await self.fix_job_repository.save_validation(
            fix_job,
            validation_status=validation_result.status,
            validation_output=validation_payload,
        )
        await self._transition(
            fix_job,
            FixJobStatus.WAITING_APPROVAL,
            data={
                "changed_files": changed_files,
                "validation_summary": validation_payload,
            },
        )

    async def _validate_and_repair_patch(
        self,
        *,
        fix_job: FixJob,
        sandbox_path: Path,
        changed_files: list[str],
    ) -> tuple[FixValidationResult, list[str]]:
        current_changed_files = changed_files
        for attempt in range(MAX_VALIDATION_REPAIR_ATTEMPTS + 1):
            validation_result = validate_fix(
                sandbox_path=sandbox_path,
                changed_files=current_changed_files,
                timeout_seconds=self.settings.analysis_subprocess_timeout_seconds,
            )
            if (
                validation_result.status != FixValidationStatus.FAILED
                or attempt >= MAX_VALIDATION_REPAIR_ATTEMPTS
            ):
                return validation_result, current_changed_files

            validation_payload = cast(
                dict[str, object],
                validation_result.model_dump(mode="json"),
            )
            repair_attempt = attempt + 1
            await publish_fix_job_progress(
                fix_job,
                message="Validation failed; repairing the generated patch.",
                data={
                    "changed_files": current_changed_files,
                    "repair_attempt": repair_attempt,
                    "validation_status": validation_result.status.value,
                    "validation_summary": validation_payload,
                },
            )
            repaired = await repair_validation_failures(
                sandbox_path=sandbox_path,
                changed_files=current_changed_files,
                validation_result=validation_result,
            )
            if not repaired:
                return validation_result, current_changed_files

            current_changed_files = get_changed_files(sandbox_path)
            if not current_changed_files:
                return validation_result, current_changed_files

            await publish_fix_job_progress(
                fix_job,
                message="Re-running validation after patch repair.",
                data={
                    "changed_files": current_changed_files,
                    "repair_attempt": repair_attempt,
                },
            )

        raise FixPipelineError("Validation repair loop ended unexpectedly")

    async def _load_selected_issues(self, fix_job: FixJob) -> list[ReviewIssue]:
        issue_ids = [UUID(issue_id) for issue_id in fix_job.issue_ids]
        issues = await self.report_repository.list_issues_by_ids(
            job_id=fix_job.review_job_id,
            issue_ids=issue_ids,
        )
        if len(issues) != len(issue_ids):
            raise FixPipelineError("Selected issues changed before fix generation")

        return issues

    async def _transition(
        self,
        fix_job: FixJob,
        status: FixJobStatus,
        *,
        data: dict[str, object] | None = None,
    ) -> FixJob:
        updated_job = await self.fix_job_repository.update_status(
            fix_job,
            status=status,
        )
        await publish_fix_job_progress(updated_job, data=data)
        return updated_job

    async def _handle_failure(self, fix_job_id: UUID, error: Exception) -> None:
        error_message = build_fix_error_message(error)
        logger.exception("Fix job %s failed: %s", fix_job_id, error_message)
        await self.fix_job_repository.rollback()
        if await self.fix_job_repository.get_by_id(fix_job_id) is None:
            logger.info(
                "Fix job %s was removed before failure could persist",
                fix_job_id,
            )
            return

        await self.fix_job_repository.mark_failed_by_id(
            fix_job_id,
            error_message=error_message,
        )
        failed_job = await self.fix_job_repository.get_by_id(fix_job_id)
        if failed_job is not None:
            await publish_fix_job_progress(
                failed_job,
                message=error_message,
                data={"failure_reason": error_message},
            )
