"""Celery worker pipeline for real repository analysis jobs."""

import asyncio
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import cast
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import run_ai_review
from app.ai.rag.code_embedding import (
    CodeEmbeddingStore,
    code_embedding_model_version,
    prepare_code_embedding_chunks,
)
from app.ai.rag.vectorstore import validate_rag_dependencies
from app.ai.review_plan import get_review_mode
from app.ai.roadmap.selection import parse_roadmap_profile
from app.analyzers.code_chunker import (
    CodeChunk,
    chunk_plain_text_file,
    chunk_python_file,
)
from app.analyzers.file_filter import filter_files, to_relative_posix_path
from app.analyzers.secret_scanner import scan_secrets
from app.analyzers.static_analysis.bandit_analyzer import run_bandit
from app.analyzers.static_analysis.base import StaticAnalysisRun, filter_files_by_suffix
from app.analyzers.static_analysis.eslint_analyzer import run_eslint
from app.analyzers.static_analysis.ruff_analyzer import run_ruff
from app.analyzers.structure_analyzer import (
    LANGUAGE_BY_EXTENSION,
    StructureAnalysisResult,
    analyze_structure,
)
from app.core.config import BACKEND_DIR, Settings
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    RepoSummaryResultRepository,
    ToolCallLogRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.mongodb import (
    FileAnalysisResultDocument,
    FileTreeEntry,
    ChunkMetadataDocument,
    ParsedStaticIssue,
    RawStaticAnalysisOutputDocument,
    RepoSummaryResultDocument,
    ToolCallLogDocument,
)
from app.schemas.normalized_issue import NormalizedIssue
from app.services.notification_service import publish_job_progress
from app.services.repo_summary_service import RepoSummaryService
from app.services.report_generation_service import AI_REPORT_MODEL, build_static_report

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 120


class ReviewPipelineError(Exception):
    """Expected pipeline failure with a user-facing error message."""


class ReviewJobCanceled(Exception):
    """Raised when a review job was removed while the worker was running."""


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
        chunk_metadata_repository: ChunkMetadataRepository,
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
        self.chunk_metadata_repository = chunk_metadata_repository
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
        await self._transition(
            review_job,
            ReviewJobStatus.CLONING,
            10,
            "Cloning repository",
        )
        clone_repository(review_job, sandbox_path)
        await self._ensure_job_active(review_job.id)
        validate_repo_size(
            sandbox_path,
            max_size_bytes=self.settings.max_repo_size_mb * 1024 * 1024,
        )
        commit_sha = get_commit_sha(sandbox_path)
        review_job = await self.review_job_repository.update_clone_metadata(
            review_job,
            commit_sha=commit_sha,
        )
        await self._publish_status(
            review_job, ReviewJobStatus.CLONING, 20, "Repository cloned"
        )

        filtered_files = filter_files(
            sandbox_path,
            max_source_file_size_bytes=self.settings.max_source_file_size_bytes,
        )
        await self._ensure_job_active(review_job.id)
        structure = await self._analyze_structure(
            review_job, sandbox_path, filtered_files
        )
        await self._ensure_job_active(review_job.id)
        await self._generate_repo_summary(review_job, sandbox_path)
        await self._ensure_job_active(review_job.id)
        issues = await self._run_static_analysis(
            review_job, sandbox_path, filtered_files
        )
        issues.extend(scan_secrets(sandbox_path, filtered_files))
        attach_source_context(issues, sandbox_path)
        await self._ensure_job_active(review_job.id)
        await self._chunk_code(
            review_job,
            sandbox_path,
            filtered_files,
            issues,
        )
        await self._ensure_job_active(review_job.id)
        await self._persist_pre_agent_report(
            review_job,
            structure,
            filtered_files,
            issues,
        )
        await self._ensure_job_active(review_job.id)
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
            35,
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
            45,
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
            50,
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
                55,
                "Repository summary generation failed; continuing review",
            )
            return

        await self._publish_status(
            review_job,
            ReviewJobStatus.GENERATING_SUMMARY,
            55,
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
            60,
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
            75,
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
        chunk_started_at = time.perf_counter()
        await self._transition(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            80,
            "Chunking source files",
        )
        python_files = [
            file_path for file_path in filtered_files if file_path.suffix == ".py"
        ]
        python_file_set = set(python_files)
        chunk_documents: list[ChunkMetadataDocument] = []
        logger.info(
            "Review job %s chunking started: %d filtered files, %d Python files",
            review_job.id,
            len(filtered_files),
            len(python_files),
        )
        for file_path in python_files:
            file_chunks = chunk_python_file(
                file_path,
                project_root=sandbox_path,
                static_issues=issues,
            )
            logger.debug(
                "Review job %s chunked Python file %s into %d chunks",
                review_job.id,
                to_relative_posix_path(file_path, sandbox_path),
                len(file_chunks),
            )
            for chunk in file_chunks:
                metadata = chunk.metadata
                chunk_document = ChunkMetadataDocument(
                    job_id=review_job.id,
                    file_path=metadata.file_path,
                    language=metadata.language,
                    chunk_type=metadata.chunk_type,
                    chunk_index=metadata.chunk_index,
                    total_chunks=metadata.total_chunks,
                    function_name=metadata.function_name,
                    class_name=metadata.class_name,
                    line_start=metadata.line_start,
                    line_end=metadata.line_end,
                    imports=metadata.imports,
                    module=metadata.module,
                    risk_area=metadata.risk_area,
                    has_static_issues=metadata.has_static_issues,
                    token_count=metadata.token_count,
                    chunk_text=chunk.content,
                )
                chunk_documents.append(chunk_document)

        for file_path in filtered_files:
            if file_path in python_file_set:
                continue

            plain_chunks = build_plain_file_chunk_metadata_documents(
                job_id=review_job.id,
                sandbox_path=sandbox_path,
                file_path=file_path,
                issues=issues,
            )
            for plain_metadata in plain_chunks:
                chunk_documents.append(plain_metadata)

        branch = review_branch(review_job)
        chunks_to_embed = prepare_code_embedding_chunks(
            chunk_documents,
            repository_id=review_job.repository_id,
            branch=branch,
            commit_sha=review_job.commit_sha,
            provider=self.settings.code_embedding_provider,
            model_name=self.settings.code_embedding_model,
            model_version=code_embedding_model_version(
                self.settings.code_embedding_provider
            ),
            dimension=self.settings.code_embedding_dimension,
        )
        for chunk_document in chunks_to_embed:
            await self.chunk_metadata_repository.insert_one(chunk_document)

        logger.info(
            "Review job %s persisted %d source chunks to MongoDB in %.2fs",
            review_job.id,
            len(chunks_to_embed),
            time.perf_counter() - chunk_started_at,
        )
        embedding_started_at = time.perf_counter()
        logger.info(
            "Review job %s semantic code indexing started for %d chunks",
            review_job.id,
            len(chunks_to_embed),
        )
        embedding_summary = await asyncio.to_thread(
            self.code_embedding_store.index_chunks,
            chunks_to_embed,
            repository_id=review_job.repository_id,
            branch=branch,
            commit_sha=review_job.commit_sha,
        )
        await self._write_embedding_trace_events(review_job.id, embedding_summary)
        logger.info(
            "Review job %s semantic code indexing finished in %.2fs",
            review_job.id,
            time.perf_counter() - embedding_started_at,
        )
        await self._publish_status(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            84,
            "Source chunks ready",
        )

    async def _write_embedding_trace_events(
        self,
        job_id: UUID,
        embedding_summary: object,
    ) -> None:
        batches = getattr(embedding_summary, "batches", None)
        if not isinstance(batches, list):
            return

        database = cast(
            AsyncIOMotorDatabase,
            self.chunk_metadata_repository.collection.database,
        )
        repository = ToolCallLogRepository(database)
        session_id = uuid4()
        if not batches:
            await repository.insert_one(
                ToolCallLogDocument(
                    job_id=job_id,
                    session_id=session_id,
                    agent_type="review",
                    sequence=1,
                    tool_name="code_embedding_cache_summary",
                    called_at=datetime.now(UTC),
                    duration_ms=max(
                        0,
                        int(getattr(embedding_summary, "duration_ms", 0)),
                    ),
                    input={
                        "total_current_count": int(
                            getattr(embedding_summary, "total_current_count", 0)
                        ),
                    },
                    output={
                        "status": "ok",
                        "embedded_count": int(
                            getattr(embedding_summary, "embedded_count", 0)
                        ),
                        "cache_hit_count": int(
                            getattr(embedding_summary, "cache_hit_count", 0)
                        ),
                        "pruned_count": int(
                            getattr(embedding_summary, "pruned_count", 0)
                        ),
                    },
                    event_type="embedding",
                    provider=self.settings.code_embedding_provider,
                    model=self.settings.code_embedding_model,
                    phase="code_embedding",
                    token_usage=None,
                    status="ok",
                    metadata={"source": "review_pipeline"},
                )
            )
            return

        for sequence, batch in enumerate(batches, start=1):
            token_usage = {
                "estimated_input_tokens": getattr(
                    batch,
                    "estimated_input_tokens",
                    0,
                )
            }
            actual_usage = getattr(batch, "token_usage", None)
            if isinstance(actual_usage, dict):
                token_usage.update(actual_usage)
            await repository.insert_one(
                ToolCallLogDocument(
                    job_id=job_id,
                    session_id=session_id,
                    agent_type="review",
                    sequence=sequence,
                    tool_name="code_embedding_batch",
                    called_at=datetime.now(UTC),
                    duration_ms=max(0, int(getattr(batch, "duration_ms", 0))),
                    input={
                        "batch_number": int(getattr(batch, "batch_number", sequence)),
                        "total_batches": int(getattr(batch, "total_batches", 0)),
                        "file_paths": list(getattr(batch, "file_paths", [])),
                    },
                    output={
                        "status": "ok",
                        "chunk_count": int(getattr(batch, "chunk_count", 0)),
                        "embedded_count": int(
                            getattr(embedding_summary, "embedded_count", 0)
                        ),
                        "cache_hit_count": int(
                            getattr(embedding_summary, "cache_hit_count", 0)
                        ),
                        "pruned_count": int(
                            getattr(embedding_summary, "pruned_count", 0)
                        ),
                        "total_current_count": int(
                            getattr(embedding_summary, "total_current_count", 0)
                        ),
                    },
                    event_type="embedding",
                    provider=self.settings.code_embedding_provider,
                    model=self.settings.code_embedding_model,
                    phase="code_embedding",
                    token_usage=token_usage,
                    status="ok",
                    metadata={"source": "review_pipeline"},
                )
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
            88,
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


def clone_repository(review_job: ReviewJob, sandbox_path: Path) -> None:
    """Clone a repository into its sandbox path."""

    cleanup_sandbox(sandbox_path, sandbox_path.parent)
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    repository_url = review_job.repository.url
    branch = review_job.branch or review_job.repository.default_branch
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        "--single-branch",
        repository_url,
        str(sandbox_path),
    ]
    completed_process = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        text=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if completed_process.returncode != 0:
        detail = completed_process.stderr.strip() or completed_process.stdout.strip()
        raise ReviewPipelineError(f"Git clone failed: {detail}")


def review_branch(review_job: ReviewJob) -> str:
    """Return the exact branch name used for this review."""

    branch = review_job.branch or review_job.repository.default_branch
    return branch.strip()


def validate_repo_size(sandbox_path: Path, *, max_size_bytes: int) -> None:
    """Reject cloned repositories that exceed the configured sandbox size."""

    total_size = sum(
        file_path.stat().st_size
        for file_path in sandbox_path.rglob("*")
        if file_path.is_file()
    )
    if total_size <= max_size_bytes:
        return

    size_mb = total_size / 1024 / 1024
    max_size_mb = max_size_bytes / 1024 / 1024
    raise ReviewPipelineError(
        f"Repository is too large: {size_mb:.1f}MB exceeds {max_size_mb:.0f}MB"
    )


def get_commit_sha(sandbox_path: Path) -> str:
    """Return the current cloned commit SHA."""

    completed_process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=sandbox_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed_process.returncode != 0:
        raise ReviewPipelineError("Unable to read cloned repository commit SHA")

    return completed_process.stdout.strip()


def build_static_analysis_runs(
    sandbox_path: Path,
    filtered_files: list[Path],
    *,
    timeout_seconds: int,
) -> list[StaticAnalysisRun]:
    """Run supported static analyzers for matching filtered files."""

    python_files = filter_files_by_suffix(filtered_files, {".py"})
    javascript_files = filter_files_by_suffix(
        filtered_files,
        {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
    )
    return [
        run_ruff(sandbox_path, python_files, timeout_seconds=timeout_seconds),
        run_bandit(sandbox_path, python_files, timeout_seconds=timeout_seconds),
        run_eslint(
            sandbox_path,
            javascript_files,
            config_path=BACKEND_DIR / "eslint.config.mjs",
            timeout_seconds=timeout_seconds,
        ),
    ]


def get_rule_profile(options: dict[str, object] | None) -> dict[str, object] | None:
    """Return the explicit roadmap profile; null means roadmap is disabled."""

    try:
        profile = parse_roadmap_profile(options)
    except ValueError as error:
        raise ReviewPipelineError(str(error)) from error
    if profile is None:
        return None

    output: dict[str, object] = {"id": profile.profile_id}
    if profile.weeks_included is not None:
        output["weeks_included"] = list(profile.weeks_included)
    return output


def build_raw_static_document(
    job_id: UUID,
    analysis_run: StaticAnalysisRun,
) -> RawStaticAnalysisOutputDocument:
    """Build the MongoDB raw output document for one analyzer run."""

    return RawStaticAnalysisOutputDocument(
        job_id=job_id,
        tool=analysis_run.tool,
        language=analysis_run.language,
        ran_at=datetime.now(UTC),
        exit_code=analysis_run.exit_code,
        stdout=analysis_run.stdout,
        stderr=analysis_run.stderr,
        duration_ms=analysis_run.duration_ms,
        parsed_issues=[
            ParsedStaticIssue(
                file_path=issue.file_path or "",
                line_start=issue.line_start,
                rule_id=get_rule_id(issue.raw_output),
                message=issue.description,
                severity=issue.severity.value,
                category=issue.category.value,
            )
            for issue in analysis_run.issues
        ],
    )


def get_rule_id(raw_output: dict[str, object] | None) -> str | None:
    """Extract a static analyzer rule id from raw output when available."""

    if raw_output is None:
        return None

    for key in ("code", "test_id", "ruleId"):
        value = raw_output.get(key)
        if value is not None:
            return str(value)

    return None


def attach_source_context(
    issues: list[NormalizedIssue],
    sandbox_path: Path,
    *,
    context_radius: int = 2,
) -> None:
    """Attach nearby source lines to issue raw output before sandbox cleanup."""

    sandbox_root = sandbox_path.resolve()
    for issue in issues:
        if issue.file_path is None or issue.line_start is None:
            continue

        source_path = (sandbox_root / issue.file_path).resolve()
        try:
            source_path.relative_to(sandbox_root)
        except ValueError:
            continue

        if not source_path.is_file():
            continue

        lines = source_path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines()
        if not lines:
            continue

        first_line = max(1, issue.line_start - context_radius)
        last_line = min(
            len(lines),
            (issue.line_end or issue.line_start) + context_radius,
        )
        context_lines = lines[first_line - 1 : last_line]
        raw_output = dict(issue.raw_output or {})
        raw_output["source_context"] = {
            "start_line": first_line,
            "lines": context_lines,
        }
        issue.raw_output = raw_output


def build_plain_file_chunk_metadata(
    *,
    job_id: UUID,
    sandbox_path: Path,
    file_path: Path,
    issues: list[NormalizedIssue],
) -> ChunkMetadataDocument:
    """Return the first generated plain-file chunk for compatibility tests."""

    chunks = build_plain_file_chunk_metadata_documents(
        job_id=job_id,
        sandbox_path=sandbox_path,
        file_path=file_path,
        issues=issues,
    )
    if not chunks:
        raise ValueError(f"Plain file is not reviewable: {file_path}")

    return chunks[0]


def build_plain_file_chunk_metadata_documents(
    *,
    job_id: UUID,
    sandbox_path: Path,
    file_path: Path,
    issues: list[NormalizedIssue],
) -> list[ChunkMetadataDocument]:
    """Represent a non-Python text source file as reviewable AI chunks."""

    if not should_chunk_plain_file(file_path):
        return []

    language = LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower()) or "text"
    chunks = chunk_plain_text_file(
        file_path,
        language=language,
        project_root=sandbox_path,
        static_issues=issues,
    )
    return [
        _chunk_metadata_document(job_id=job_id, chunk=chunk)
        for chunk in chunks
        if chunk.content.strip()
    ]


def should_chunk_plain_file(file_path: Path) -> bool:
    """Return True when a non-Python file should enter semantic code chunks."""

    if file_path.suffix.lower() != ".md":
        return True

    return file_path.name.lower() in {"readme.md", "readme.markdown"}


def _chunk_metadata_document(
    *, job_id: UUID, chunk: CodeChunk
) -> ChunkMetadataDocument:
    metadata = chunk.metadata
    return ChunkMetadataDocument(
        job_id=job_id,
        file_path=metadata.file_path,
        language=metadata.language,
        chunk_type=metadata.chunk_type,
        chunk_index=metadata.chunk_index,
        total_chunks=metadata.total_chunks,
        function_name=metadata.function_name,
        class_name=metadata.class_name,
        line_start=metadata.line_start,
        line_end=metadata.line_end,
        imports=metadata.imports,
        module=metadata.module,
        risk_area=metadata.risk_area,
        has_static_issues=metadata.has_static_issues,
        token_count=metadata.token_count,
        chunk_text=chunk.content,
    )


def build_flat_file_tree_entries(
    sandbox_path: Path,
    filtered_files: list[Path],
) -> list[FileTreeEntry]:
    """Build flat MongoDB file entries from filtered files."""

    return [
        FileTreeEntry(
            path=to_relative_posix_path(file_path, sandbox_path),
            language=LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower()),
            size_bytes=file_path.stat().st_size,
            line_count=count_lines(file_path),
            should_review=True,
        )
        for file_path in filtered_files
    ]


def count_lines(file_path: Path) -> int:
    """Count text lines without failing the whole analysis for one file."""

    try:
        return len(file_path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def cleanup_sandbox(sandbox_path: Path, sandbox_root: Path) -> None:
    """Remove one sandbox path after confirming it is under the sandbox root."""

    resolved_root = sandbox_root.resolve()
    resolved_path = sandbox_path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ReviewPipelineError(f"Refusing to cleanup unsafe path: {resolved_path}")

    if resolved_path.exists():
        shutil.rmtree(resolved_path)


def build_error_message(error: Exception) -> str:
    """Return a stable user-facing pipeline error message."""

    if isinstance(error, ReviewPipelineError):
        return str(error)

    if isinstance(error, subprocess.TimeoutExpired):
        return f"Command timed out after {error.timeout}s"

    return f"Review pipeline failed: {error}"
