"""Orchestrate the repository analysis pipeline."""

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rag.code_embedding import CodeEmbeddingStore
from app.ai.rag.vectorstore import validate_rag_dependencies
from app.ai.review.agent import run_ai_review
from app.ai.review.plan import get_review_mode
from app.analyzers.file_filter import build_file_manifest
from app.analyzers.secret_scanner import scan_secrets
from app.analyzers.structure_analyzer import (
    StructureAnalysisResult,
    analyze_structure,
)
from app.core.config import Settings
from app.core.review_targets import is_review_target_path
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.repositories.mongodb_repository import (
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    RepoSummaryResultRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.mongodb import (
    FileAnalysisResultDocument,
    RepoSummaryResultDocument,
)
from app.schemas.normalized_issue import NormalizedIssue
from app.services.code_indexing.service import CodeIndexingService
from app.services.repo_summary.service import RepoSummaryService
from app.services.reporting.generation import AI_REPORT_MODEL, build_static_report
from app.services.review_jobs.notifications import publish_job_progress
from app.services.review_jobs.pipeline.artifacts import (
    attach_source_context,
    build_flat_file_tree_entries,
    build_raw_static_document,
    build_static_analysis_runs,
    get_rule_profile,
)
from app.services.review_jobs.pipeline.errors import (
    ReviewJobCanceled,
    ReviewPipelineError,
    build_error_message,
)
from app.services.review_jobs.pipeline.workspace import (
    clone_repository,
    get_commit_sha,
)
from app.services.sandbox.workspace import validate_repository_size

logger = logging.getLogger(__name__)

AI_REVIEW_PROGRESS_START = 50
AI_REVIEW_PROGRESS_END = 92

__all__ = [
    "ReviewJobCanceled",
    "ReviewPipelineError",
    "ReviewPipelineService",
    "StructureAnalysisResult",
    "build_error_message",
    "get_rule_profile",
]


class ReviewPipelineService:
    """Orchestrate clone, analysis, persistence, and progress updates."""

    def __init__(
        self,
        *,
        settings: Settings,
        review_job_repository: ReviewJobRepository,
        repository_repository: RepositoryRepository,
        report_repository: ReportRepository,
        file_analysis_repository: FileAnalysisResultRepository,
        repo_summary_repository: RepoSummaryResultRepository,
        raw_static_repository: RawStaticAnalysisOutputRepository,
        code_indexing_service: CodeIndexingService,
        code_embedding_store: CodeEmbeddingStore,
        postgres_session: AsyncSession,
    ) -> None:
        self.settings = settings
        self.review_job_repository = review_job_repository
        self.repository_repository = repository_repository
        self.report_repository = report_repository
        self.file_analysis_repository = file_analysis_repository
        self.repo_summary_repository = repo_summary_repository
        self.raw_static_repository = raw_static_repository
        self.code_indexing_service = code_indexing_service
        self.code_embedding_store = code_embedding_store
        self.postgres_session = postgres_session

    async def run(self, job_id: UUID) -> None:
        """Run the full real-repo review pipeline for one job."""

        started_at = time.perf_counter()
        sandbox_path = Path(self.settings.sandbox_root) / str(job_id)
        review_job = await self.review_job_repository.get_by_id(job_id)
        if review_job is None:
            logger.info("Review job %s no longer exists; skipping worker run", job_id)
            return

        try:
            await self.review_job_repository.mark_started(
                review_job,
                sandbox_path=str(sandbox_path),
            )
            await self._run_pipeline_steps(review_job, sandbox_path)
            elapsed_seconds = time.perf_counter() - started_at
            logger.info("Review job %s completed in %.2fs", job_id, elapsed_seconds)
        except ReviewJobCanceled:
            logger.info("Review job %s was canceled during worker execution", job_id)
        except Exception as error:
            await self._handle_failure(job_id, error)

    async def _run_pipeline_steps(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> None:
        await self._ensure_job_active(review_job.id)
        get_review_mode(review_job.options)

        review_job = await self._clone_and_prepare_repository(
            review_job,
            sandbox_path,
        )
        filtered_files = self._build_filtered_file_manifest(review_job, sandbox_path)
        await self._prepare_pre_agent_review(
            review_job,
            sandbox_path,
            filtered_files,
        )
        await self._run_ai_review_and_complete(review_job, sandbox_path)

    async def _prepare_pre_agent_review(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
    ) -> None:
        review_target_files = [
            file_path
            for file_path in filtered_files
            if is_review_target_path(file_path)
        ]
        await self._ensure_job_active(review_job.id)
        structure = await self._analyze_structure(
            review_job,
            sandbox_path,
            filtered_files,
        )

        await self._ensure_job_active(review_job.id)
        await self._generate_repo_summary(review_job, sandbox_path)

        await self._ensure_job_active(review_job.id)
        issues = await self._collect_static_issues(
            review_job,
            sandbox_path,
            review_target_files,
        )

        await self._ensure_job_active(review_job.id)
        await self._chunk_code(
            review_job,
            sandbox_path,
            review_target_files,
            issues,
        )

        await self._ensure_job_active(review_job.id)
        await self._persist_pre_agent_report(
            review_job,
            structure,
            review_target_files,
            issues,
        )

    async def _clone_and_prepare_repository(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> ReviewJob:
        await self._transition(
            review_job,
            ReviewJobStatus.CLONING,
            5,
            "Cloning repository",
        )
        clone_repository(review_job, sandbox_path)
        await self._ensure_job_active(review_job.id)
        validate_repository_size(
            sandbox_path,
            max_size_bytes=self.settings.max_repo_size_mb * 1024 * 1024,
        )
        commit_sha = get_commit_sha(sandbox_path)
        updated_job = await self.review_job_repository.update_clone_metadata(
            review_job,
            commit_sha=commit_sha,
        )
        await self._publish_status(
            updated_job,
            ReviewJobStatus.CLONING,
            10,
            "Repository cloned",
        )
        return updated_job

    def _build_filtered_file_manifest(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> list[Path]:
        file_manifest = build_file_manifest(
            sandbox_path,
            max_source_file_size_bytes=self.settings.max_source_file_size_bytes,
        )
        filtered_files = file_manifest.files
        logger.info(
            "Review job %s file manifest built from %s: %d selected / %d scanned",
            review_job.id,
            file_manifest.source,
            len(filtered_files),
            file_manifest.scanned_count,
        )
        return filtered_files

    async def _collect_static_issues(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
    ) -> list[NormalizedIssue]:
        issues = await self._run_static_analysis(
            review_job, sandbox_path, filtered_files
        )
        issues.extend(scan_secrets(sandbox_path, filtered_files))
        attach_source_context(issues, sandbox_path)
        return issues

    async def _run_ai_review_and_complete(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> None:
        await self._run_ai_agent(review_job, sandbox_path)
        await self._ensure_job_active(review_job.id)
        await self._require_ai_generated_report(review_job.id)
        await self._transition(
            review_job,
            ReviewJobStatus.GENERATING_REPORT,
            95,
            "Final report generated",
        )

        await self._transition(
            review_job,
            ReviewJobStatus.COMPLETED,
            100,
            "Review complete",
        )

    async def _analyze_structure(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
    ) -> StructureAnalysisResult:
        await self._transition(
            review_job,
            ReviewJobStatus.ANALYZING_STRUCTURE,
            12,
            "Analyzing project structure",
        )
        structure = analyze_structure(sandbox_path, filtered_files)
        structure.project_structure["file_tree"] = structure.file_tree
        await self.file_analysis_repository.insert_one(
            FileAnalysisResultDocument(
                job_id=review_job.id,
                analyzed_at=datetime.now(UTC),
                project_structure=structure.project_structure,
                file_tree=build_flat_file_tree_entries(sandbox_path, filtered_files),
            )
        )
        await self._publish_status(
            review_job,
            ReviewJobStatus.ANALYZING_STRUCTURE,
            18,
            "Project structure analyzed",
        )
        return structure

    async def _generate_repo_summary(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> None:
        await self._transition(
            review_job,
            ReviewJobStatus.GENERATING_SUMMARY,
            20,
            "Generating repository summary",
        )
        try:
            repo_summary_service = RepoSummaryService()
            prompt = repo_summary_service.build_prompt(sandbox_path)
            summary = await repo_summary_service.generate_summary(prompt)
            await self.repo_summary_repository.insert_one(
                RepoSummaryResultDocument(
                    repository_id=review_job.repository_id,
                    job_id=review_job.id,
                    commit_sha=review_job.commit_sha or "",
                    generated_at=datetime.now(UTC),
                    model_used=self.settings.openai_model,
                    **summary.model_dump(),
                )
            )
        except Exception as error:
            logger.exception(
                "Repo Summary generation failed for review job %s; continuing: %s",
                review_job.id,
                error,
            )
            await self._publish_status(
                review_job,
                ReviewJobStatus.GENERATING_SUMMARY,
                25,
                "Repository summary generation failed; continuing review",
            )
            return

        await self._publish_status(
            review_job,
            ReviewJobStatus.GENERATING_SUMMARY,
            25,
            "Repository summary generated",
        )

    async def _run_static_analysis(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
    ) -> list[NormalizedIssue]:
        await self._transition(
            review_job,
            ReviewJobStatus.RUNNING_STATIC_ANALYSIS,
            28,
            "Running static analyzers",
        )
        runs = build_static_analysis_runs(
            sandbox_path,
            filtered_files,
            timeout_seconds=self.settings.analysis_subprocess_timeout_seconds,
        )
        for analysis_run in runs:
            await self.raw_static_repository.insert_one(
                build_raw_static_document(review_job.id, analysis_run)
            )

        await self._publish_status(
            review_job,
            ReviewJobStatus.RUNNING_STATIC_ANALYSIS,
            38,
            "Static analyzers complete",
        )
        return [issue for analysis_run in runs for issue in analysis_run.issues]

    async def _chunk_code(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
        issues: list[NormalizedIssue],
    ) -> None:
        await self._transition(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            40,
            "Chunking source files",
        )
        await self.code_indexing_service.index(
            review_job=review_job,
            sandbox_path=sandbox_path,
            filtered_files=filtered_files,
            issues=issues,
        )
        await self._publish_status(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            48,
            "Source chunks ready",
        )

    async def _persist_pre_agent_report(
        self,
        review_job: ReviewJob,
        structure: StructureAnalysisResult,
        filtered_files: list[Path],
        issues: list[NormalizedIssue],
    ) -> None:
        report = build_static_report(
            job_id=review_job.id,
            total_files_analyzed=len(filtered_files),
            issues=issues,
            tech_stack={
                "languages": structure.project_structure.get("languages", {}),
                "frameworks": structure.project_structure.get("frameworks", []),
                "tools": ["ruff", "bandit", "eslint", "secret_scanner"],
            },
        )
        await self.report_repository.replace_analysis_results(
            report=report,
            issues=issues,
        )

    async def _run_ai_agent(self, review_job: ReviewJob, sandbox_path: Path) -> None:
        validate_rag_dependencies()
        await self._transition(
            review_job,
            ReviewJobStatus.AI_REVIEWING,
            AI_REVIEW_PROGRESS_START,
            "Running AI review agent",
        )
        database = cast(
            AsyncIOMotorDatabase,
            self.file_analysis_repository.collection.database,
        )
        await run_ai_review(
            job_id=review_job.id,
            sandbox_path=sandbox_path,
            postgres_session=self.postgres_session,
            mongodb_database=database,
            code_embedding_store=self.code_embedding_store,
            on_batch_completed=lambda completed, total: self._publish_ai_batch_progress(
                review_job.id, completed, total
            ),
        )

    async def _publish_ai_batch_progress(
        self,
        job_id: UUID,
        completed_batches: int,
        total_batches: int,
    ) -> None:
        progress = calculate_ai_review_progress(completed_batches, total_batches)
        await publish_job_progress(
            job_id,
            "progress_update",
            {
                "status": ReviewJobStatus.AI_REVIEWING.value,
                "progress": progress,
                "message": (
                    f"AI review batch {completed_batches} of {total_batches} completed"
                ),
                "data": {
                    "completed_batches": completed_batches,
                    "total_batches": total_batches,
                },
            },
        )

    async def _require_ai_generated_report(self, job_id: UUID) -> None:
        report = await self.report_repository.get_report_by_job_id(job_id)
        if report is None:
            raise ReviewPipelineError("AI review did not create a final report")

        if report.ai_model_used != AI_REPORT_MODEL:
            raise ReviewPipelineError(
                "AI review finished without generating the final report; "
                f"current report model is {report.ai_model_used or 'unknown'}"
            )

    async def _handle_failure(self, job_id: UUID, error: Exception) -> None:
        error_message = build_error_message(error)
        logger.exception("Review job %s failed: %s", job_id, error_message)
        await self.review_job_repository.rollback()
        if await self.review_job_repository.get_by_id(job_id) is None:
            logger.info(
                "Review job %s was removed before failure could persist", job_id
            )
            return

        await self.review_job_repository.mark_failed_by_id(
            job_id,
            error_message=error_message,
            progress=100,
        )
        await publish_job_progress(
            job_id,
            "failed",
            {
                "status": ReviewJobStatus.FAILED.value,
                "progress": 100,
                "message": error_message,
            },
        )

    async def _transition(
        self,
        review_job: ReviewJob,
        status: ReviewJobStatus,
        progress: int,
        message: str,
    ) -> None:
        await self._ensure_job_active(review_job.id)
        updated_job = await self.review_job_repository.update_status(
            review_job,
            status=status,
            message=message,
            progress=progress,
        )
        if status == ReviewJobStatus.COMPLETED:
            completed_at = updated_job.completed_at or datetime.now(UTC)
            source_repository = await self.repository_repository.get_by_id(
                updated_job.repository_id
            )
            if source_repository is not None:
                await self.repository_repository.update_last_reviewed_at(
                    source_repository,
                    completed_at,
                )

        await self._publish_status(review_job, status, progress, message)

    async def _ensure_job_active(self, job_id: UUID) -> None:
        if await self.review_job_repository.get_by_id(job_id) is None:
            raise ReviewJobCanceled

    async def _publish_status(
        self,
        review_job: ReviewJob,
        status: ReviewJobStatus,
        progress: int,
        message: str,
    ) -> None:
        event_type = (
            "completed" if status == ReviewJobStatus.COMPLETED else "status_change"
        )
        await publish_job_progress(
            review_job.id,
            event_type,
            {
                "status": status.value,
                "progress": progress,
                "message": message,
            },
        )


def calculate_ai_review_progress(
    completed_batches: int,
    total_batches: int,
) -> int:
    """Map completed AI judge batches into the reserved 88-94% range."""

    if total_batches <= 0 or completed_batches <= 0:
        return AI_REVIEW_PROGRESS_START

    bounded_completed = min(completed_batches, total_batches)
    progress_span = AI_REVIEW_PROGRESS_END - AI_REVIEW_PROGRESS_START
    completed_span = max(1, bounded_completed * progress_span // total_batches)
    return min(AI_REVIEW_PROGRESS_END, AI_REVIEW_PROGRESS_START + completed_span)
