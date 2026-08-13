"""Orchestrate generated code-fix patches."""

import logging
import time
from pathlib import Path
from typing import cast
from uuid import UUID

from app.ai.llm.config import llm_session
from app.core.config import Settings
from app.models.fix_job import FixJob, FixJobStatus, FixValidationStatus
from app.models.review_issue import ReviewIssue
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.fix_job import FixIssuePlan, FixIssueResult, FixValidationResult
from app.services.fix_jobs.notifications import publish_fix_job_progress
from app.services.fix_jobs.pipeline.contracts import FixIssueSpec
from app.services.fix_jobs.pipeline.errors import (
    FixPipelineError,
    build_fix_error_message,
)
from app.services.fix_jobs.pipeline.execution import (
    DockerFixCommandExecutor,
    FixCommandExecutor,
)
from app.services.fix_jobs.pipeline.generation import (
    generate_fix_changes,
    repair_fix_failures,
)
from app.services.fix_jobs.pipeline.planning import build_fix_issue_plans
from app.services.fix_jobs.pipeline.verification import verify_fix_issues
from app.services.fix_jobs.pipeline.workspace import (
    get_changed_files,
    get_git_diff,
    prepare_fix_workspace,
)
from app.services.sandbox.workspace import validate_repository_size

logger = logging.getLogger(__name__)

MAX_LOGIC_REPAIR_ATTEMPTS = 2
BASE_LLM_CALL_RESERVE = 4
LLM_CALLS_PER_SELECTED_ISSUE = 4
REFERENCE_PATCH_SUMMARY = (
    "Runtime validation was intentionally not run. This reference patch was reviewed "
    "for cross-file logic consistency only."
)


class FixPipelineService:
    """Orchestrate clone, patch generation, validation, and persistence."""

    def __init__(
        self,
        *,
        settings: Settings,
        fix_job_repository: FixJobRepository,
        report_repository: ReportRepository,
        executor: FixCommandExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.fix_job_repository = fix_job_repository
        self.report_repository = report_repository
        self.executor = executor or DockerFixCommandExecutor.from_settings(settings)

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
            async with llm_session():
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
        validate_repository_size(
            sandbox_path,
            max_size_bytes=self.settings.max_repo_size_mb * 1024 * 1024,
        )

        fix_job = await self._transition(fix_job, FixJobStatus.GENERATING_PATCH)
        issues = await self._load_selected_issues(fix_job)
        self._ensure_llm_call_budget(issue_count=len(issues))
        specs, plans = await build_fix_issue_plans(
            sandbox_path=sandbox_path,
            issues=issues,
        )
        plan_payload = [
            cast(dict[str, object], plan.model_dump(mode="json")) for plan in plans
        ]
        fix_job = await self.fix_job_repository.save_issue_plan(
            fix_job,
            issue_plan=plan_payload,
        )
        await publish_fix_job_progress(
            fix_job,
            message="Planned cross-file fixes for the selected issues.",
            data={
                "planned_issues": sum(plan.status.value == "planned" for plan in plans),
                "uncertain_issues": sum(
                    plan.status.value != "planned" for plan in plans
                ),
            },
        )
        await generate_fix_changes(
            sandbox_path=sandbox_path,
            issues=issues,
            specs=specs,
            plans=plans,
            timeout_seconds=self.settings.analysis_subprocess_timeout_seconds,
            executor=self.executor,
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
            message="Reviewing cross-file patch logic.",
            data={"changed_files": changed_files},
        )
        (
            validation_result,
            changed_files,
            issue_results,
        ) = await self._review_and_repair_patch(
            fix_job=fix_job,
            sandbox_path=sandbox_path,
            changed_files=changed_files,
            issues=issues,
            specs=specs,
            plans=plans,
        )
        diff = get_git_diff(sandbox_path)
        if not diff.strip():
            raise FixPipelineError("Logic repair removed all code changes")

        fix_job = await self.fix_job_repository.save_patch(
            fix_job,
            diff=diff,
            changed_files=changed_files,
        )
        results_payload = [
            cast(dict[str, object], result.model_dump(mode="json"))
            for result in issue_results
        ]
        fix_job = await self.fix_job_repository.save_issue_results(
            fix_job,
            issue_results=results_payload,
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
            message="Reference patch is ready for review.",
            data={
                "changed_files": changed_files,
                "validation_summary": validation_payload,
                "fixed_issues": sum(
                    result.verdict.value == "fixed" for result in issue_results
                ),
                "unresolved_issues": sum(
                    result.verdict.value != "fixed" for result in issue_results
                ),
            },
        )

    async def _review_and_repair_patch(
        self,
        *,
        fix_job: FixJob,
        sandbox_path: Path,
        changed_files: list[str],
        issues: list[ReviewIssue],
        specs: list[FixIssueSpec],
        plans: list[FixIssuePlan],
    ) -> tuple[FixValidationResult, list[str], list[FixIssueResult]]:
        current_changed_files = changed_files
        for attempt in range(MAX_LOGIC_REPAIR_ATTEMPTS + 1):
            issue_results = await verify_fix_issues(
                sandbox_path=sandbox_path,
                issues=issues,
                specs=specs,
                plans=plans,
                timeout_seconds=self.settings.analysis_subprocess_timeout_seconds,
                attempt=attempt + 1,
                executor=self.executor,
                scenario_results_by_issue=None,
            )
            for result in issue_results:
                result.changed_files = [
                    path
                    for path in result.planned_files
                    if path in current_changed_files
                ]
            if (
                all(result.verdict.value == "fixed" for result in issue_results)
                or attempt >= MAX_LOGIC_REPAIR_ATTEMPTS
            ):
                return (
                    self._reference_patch_result(),
                    current_changed_files,
                    issue_results,
                )

            repair_attempt = attempt + 1
            await publish_fix_job_progress(
                fix_job,
                message="Logic review found unresolved issues; repairing the patch.",
                data={
                    "changed_files": current_changed_files,
                    "repair_attempt": repair_attempt,
                    "unresolved_issues": sum(
                        result.verdict.value != "fixed" for result in issue_results
                    ),
                },
            )
            repaired = await repair_fix_failures(
                sandbox_path=sandbox_path,
                specs=specs,
                plans=plans,
                issue_results=issue_results,
                validation_result=None,
            )
            if not repaired:
                return (
                    self._reference_patch_result(),
                    current_changed_files,
                    issue_results,
                )

            current_changed_files = get_changed_files(sandbox_path)
            if not current_changed_files:
                return (
                    self._reference_patch_result(),
                    current_changed_files,
                    issue_results,
                )

            await publish_fix_job_progress(
                fix_job,
                message="Reviewing patch logic again after repair.",
                data={
                    "changed_files": current_changed_files,
                    "repair_attempt": repair_attempt,
                },
            )

        raise FixPipelineError("Logic repair loop ended unexpectedly")

    @staticmethod
    def _reference_patch_result() -> FixValidationResult:
        return FixValidationResult(
            status=FixValidationStatus.NOT_RUN,
            summary=REFERENCE_PATCH_SUMMARY,
            checks=[],
        )

    def _ensure_llm_call_budget(self, *, issue_count: int) -> None:
        estimated_calls = BASE_LLM_CALL_RESERVE + (
            issue_count * LLM_CALLS_PER_SELECTED_ISSUE
        )
        if estimated_calls > self.settings.llm_job_call_budget:
            raise FixPipelineError(
                "Selected issues require an estimated "
                f"{estimated_calls} LLM calls, exceeding LLM_JOB_CALL_BUDGET="
                f"{self.settings.llm_job_call_budget}. Split the fix into fewer issues."
            )

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
        message: str | None = None,
        data: dict[str, object] | None = None,
    ) -> FixJob:
        updated_job = await self.fix_job_repository.update_status(
            fix_job,
            status=status,
        )
        await publish_fix_job_progress(updated_job, message=message, data=data)
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
