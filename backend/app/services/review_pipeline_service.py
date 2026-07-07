"""Celery worker pipeline for real repository analysis jobs."""

from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import cast
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import run_ai_review
from app.ai.rules.roadmap_checker import RoadmapCheckOutput, RoadmapComplianceChecker
from app.analyzers.code_chunker import chunk_python_file
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
    RoadmapComplianceResultRepository,
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
)
from app.schemas.normalized_issue import NormalizedIssue
from app.services.notification_service import publish_job_progress
from app.services.report_generation_service import build_static_report

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS = 120


class ReviewPipelineError(Exception):
    """Expected pipeline failure with a user-facing error message."""


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
        raw_static_repository: RawStaticAnalysisOutputRepository,
        chunk_metadata_repository: ChunkMetadataRepository,
        roadmap_repository: RoadmapComplianceResultRepository,
        postgres_session: AsyncSession,
    ) -> None:
        self.settings = settings
        self.review_job_repository = review_job_repository
        self.repository_repository = repository_repository
        self.report_repository = report_repository
        self.file_analysis_repository = file_analysis_repository
        self.raw_static_repository = raw_static_repository
        self.chunk_metadata_repository = chunk_metadata_repository
        self.roadmap_repository = roadmap_repository
        self.postgres_session = postgres_session
        self.roadmap_checker = RoadmapComplianceChecker()

    async def run(self, job_id: UUID) -> None:
        """Run the full real-repo review pipeline for one job."""

        started_at = time.perf_counter()
        sandbox_path = Path(self.settings.sandbox_root) / str(job_id)
        review_job = await self.review_job_repository.get_by_id(job_id)
        if review_job is None:
            raise ReviewPipelineError(f"Review job not found: {job_id}")

        try:
            await self.review_job_repository.mark_started(
                review_job,
                sandbox_path=str(sandbox_path),
            )
            await self._run_pipeline_steps(review_job, sandbox_path)
            elapsed_seconds = time.perf_counter() - started_at
            logger.info("Review job %s completed in %.2fs", job_id, elapsed_seconds)
        except Exception as error:
            await self._handle_failure(job_id, error)

    async def _run_pipeline_steps(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> None:
        await self._transition(
            review_job,
            ReviewJobStatus.CLONING,
            10,
            "Cloning repository",
        )
        clone_repository(review_job, sandbox_path)
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
        structure = await self._analyze_structure(
            review_job, sandbox_path, filtered_files
        )
        roadmap_output = await self._run_roadmap_compliance(review_job, sandbox_path)
        issues = await self._run_static_analysis(
            review_job, sandbox_path, filtered_files
        )
        issues.extend(scan_secrets(sandbox_path, filtered_files))
        if roadmap_output is not None:
            issues.extend(roadmap_output.issues)
        attach_source_context(issues, sandbox_path)
        await self._chunk_code(
            review_job,
            sandbox_path,
            filtered_files,
            issues,
        )
        await self._persist_pre_agent_report(
            review_job,
            structure,
            filtered_files,
            issues,
            roadmap_output,
        )
        await self._run_ai_agent(review_job, sandbox_path)
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

    async def _run_roadmap_compliance(
        self,
        review_job: ReviewJob,
        sandbox_path: Path,
    ) -> RoadmapCheckOutput | None:
        rule_profile = get_rule_profile(review_job.options)
        return await self.roadmap_checker.run_and_persist(
            job_id=review_job.id,
            sandbox_path=sandbox_path,
            rule_profile=rule_profile,
            postgres_session=self.postgres_session,
            roadmap_repository=self.roadmap_repository,
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
        await self._transition(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            80,
            "Chunking source files",
        )
        python_files = [
            file_path for file_path in filtered_files if file_path.suffix == ".py"
        ]
        for file_path in python_files:
            for chunk in chunk_python_file(
                file_path,
                project_root=sandbox_path,
                static_issues=issues,
            ):
                metadata = chunk.metadata
                await self.chunk_metadata_repository.insert_one(
                    ChunkMetadataDocument(
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
                )
        await self._publish_status(
            review_job,
            ReviewJobStatus.CHUNKING_CODE,
            84,
            "Source chunks ready",
        )

    async def _persist_pre_agent_report(
        self,
        review_job: ReviewJob,
        structure: StructureAnalysisResult,
        filtered_files: list[Path],
        issues: list[NormalizedIssue],
        roadmap_output: RoadmapCheckOutput | None,
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
            compliance_score=roadmap_output.compliance_score
            if roadmap_output is not None
            else None,
            bonus_score=roadmap_output.bonus_score
            if roadmap_output is not None
            else None,
        )
        await self.report_repository.replace_analysis_results(
            report=report,
            issues=issues,
        )

    async def _run_ai_agent(self, review_job: ReviewJob, sandbox_path: Path) -> None:
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

    async def _handle_failure(self, job_id: UUID, error: Exception) -> None:
        error_message = build_error_message(error)
        logger.exception("Review job %s failed: %s", job_id, error_message)
        await self.review_job_repository.rollback()
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
    """Return the optional roadmap rule profile from review job options."""

    if options is None:
        return None

    rule_profile = options.get("rule_profile")
    if rule_profile is None:
        return None
    if not isinstance(rule_profile, dict):
        raise ReviewPipelineError("review job rule_profile option must be an object")

    return rule_profile


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
