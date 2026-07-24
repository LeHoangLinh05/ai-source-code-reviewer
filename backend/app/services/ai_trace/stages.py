"""Build user-facing pipeline stages from AI trace coverage."""

from __future__ import annotations

from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.schemas.ai_trace import AIToolCallTrace, AITraceCoverage, AITraceStage
from app.services.reporting.generation import AI_REPORT_MODEL, STATIC_REPORT_MODEL


def build_trace_stages(
    *,
    job: ReviewJob | None,
    coverage: AITraceCoverage,
    has_ai_started: bool,
    latest_tool_call: AIToolCallTrace | None,
    report: ReviewReport | None,
) -> list[AITraceStage]:
    """Translate persisted pipeline state into ordered presentation stages."""

    status = job.status if job else None
    is_failed = status == ReviewJobStatus.FAILED
    report_model = report.ai_model_used if report else None
    latest_tool_name = latest_tool_call.tool_name if latest_tool_call else None

    return [
        AITraceStage(
            key="clone",
            label="Clone repository",
            status=_stage_status(
                status,
                ReviewJobStatus.CLONING,
                completed=bool(job and job.commit_sha),
                failed=is_failed,
            ),
            detail=(
                job.commit_sha[:7]
                if job and job.commit_sha
                else "Waiting for repository clone"
            ),
            progress_percent=100.0 if job and job.commit_sha else 0.0,
        ),
        AITraceStage(
            key="structure",
            label="Map files",
            status=_stage_status(
                status,
                ReviewJobStatus.ANALYZING_STRUCTURE,
                completed=coverage.total_reviewable_files > 0,
                failed=is_failed,
            ),
            detail=(
                f"{coverage.total_reviewable_files} files, "
                f"{coverage.total_reviewable_lines} lines selected"
            ),
            progress_percent=100.0 if coverage.total_reviewable_files > 0 else 0.0,
            current=coverage.total_reviewable_files,
            total=coverage.total_reviewable_files,
        ),
        AITraceStage(
            key="static",
            label="Static analyzers",
            status=_stage_status(
                status,
                ReviewJobStatus.RUNNING_STATIC_ANALYSIS,
                completed=coverage.static_analyzer_runs > 0,
                failed=is_failed,
            ),
            detail=(
                f"{coverage.static_analyzer_runs} runs, "
                f"{coverage.static_analyzer_issues} parsed findings"
            ),
            progress_percent=100.0 if coverage.static_analyzer_runs else 0.0,
            current=coverage.static_analyzer_runs,
            total=max(coverage.static_analyzer_runs, 3),
        ),
        AITraceStage(
            key="chunks",
            label="Chunk source",
            status=_chunk_stage_status(
                status,
                failed=is_failed,
                total_chunks=coverage.total_chunks,
            ),
            detail=_chunk_stage_detail(coverage),
            progress_percent=100.0 if coverage.total_chunks else 0.0,
            current=coverage.total_chunks,
            total=coverage.total_chunks,
        ),
        AITraceStage(
            key="ai",
            label="AI agent review",
            status=_ai_stage_status(
                status=status,
                has_ai_started=has_ai_started,
                coverage=coverage,
                report_model=report_model,
            ),
            detail=_ai_stage_detail(
                latest_tool_name=latest_tool_name,
                coverage=coverage,
                report_model=report_model,
            ),
            progress_percent=coverage.ai_read_chunk_percent,
            current=coverage.ai_read_target_chunks,
            total=coverage.target_chunks,
        ),
        AITraceStage(
            key="report",
            label="Report handoff",
            status=_report_stage_status(status, coverage, report_model),
            detail=_report_stage_detail(coverage, report_model),
            progress_percent=100.0 if report is not None else 0.0,
        ),
    ]


def _is_full_ai_review(
    *,
    report_model: str | None,
    coverage_ai_chunks: int,
    total_chunks: int,
) -> bool:
    _ = coverage_ai_chunks, total_chunks
    return report_model == AI_REPORT_MODEL


def _stage_status(
    current_status: ReviewJobStatus | None,
    running_status: ReviewJobStatus,
    *,
    completed: bool,
    failed: bool,
) -> str:
    if failed and not completed:
        return "failed"
    if completed:
        return "completed"
    if current_status == running_status:
        return "running"
    return "pending"


def _chunk_stage_status(
    current_status: ReviewJobStatus | None,
    *,
    failed: bool,
    total_chunks: int,
) -> str:
    if total_chunks > 0:
        return "completed"
    if failed:
        return "failed"
    if current_status == ReviewJobStatus.CHUNKING_CODE:
        return "running"
    if current_status in {
        ReviewJobStatus.AI_REVIEWING,
        ReviewJobStatus.GENERATING_REPORT,
        ReviewJobStatus.COMPLETED,
    }:
        return "skipped"
    return "pending"


def _chunk_stage_detail(coverage: AITraceCoverage) -> str:
    if coverage.total_chunks > 0:
        return f"{coverage.total_chunks} chunks from {coverage.chunked_files} files"

    return "No review chunk metadata was persisted"


def _ai_stage_status(
    *,
    status: ReviewJobStatus | None,
    has_ai_started: bool,
    coverage: AITraceCoverage,
    report_model: str | None,
) -> str:
    if _is_full_ai_review(
        report_model=report_model,
        coverage_ai_chunks=coverage.ai_read_target_chunks,
        total_chunks=coverage.target_chunks,
    ):
        return "completed"
    if report_model == AI_REPORT_MODEL:
        return "warning"
    if status == ReviewJobStatus.AI_REVIEWING:
        return "running"
    if status == ReviewJobStatus.FAILED and not has_ai_started:
        return "failed"
    if has_ai_started:
        return "warning"
    return "pending"


def _ai_stage_detail(
    *,
    latest_tool_name: str | None,
    coverage: AITraceCoverage,
    report_model: str | None,
) -> str:
    if report_model == AI_REPORT_MODEL:
        return (
            f"Used {coverage.ai_read_chunks} semantic/source chunks and "
            f"created {coverage.generated_ai_issues} AI issues"
        )
    if latest_tool_name:
        return (
            f"Used {coverage.ai_read_chunks} semantic/source chunks; latest "
            f"activity is {latest_tool_name}"
        )
    return ""


def _report_stage_status(
    current_status: ReviewJobStatus | None,
    coverage: AITraceCoverage,
    report_model: str | None,
) -> str:
    if _is_full_ai_review(
        report_model=report_model,
        coverage_ai_chunks=coverage.ai_read_target_chunks,
        total_chunks=coverage.target_chunks,
    ):
        return "completed"
    if report_model in {AI_REPORT_MODEL, STATIC_REPORT_MODEL}:
        return "warning"
    if current_status == ReviewJobStatus.GENERATING_REPORT:
        return "running"
    if current_status == ReviewJobStatus.FAILED:
        return "failed"
    return "pending"


def _report_stage_detail(coverage: AITraceCoverage, report_model: str | None) -> str:
    if (
        _is_full_ai_review(
            report_model=report_model,
            coverage_ai_chunks=coverage.ai_read_target_chunks,
            total_chunks=coverage.target_chunks,
        )
        or report_model == AI_REPORT_MODEL
    ):
        return "Final report was generated by the AI agent"
    if report_model == STATIC_REPORT_MODEL:
        return "Waiting for the AI final report"
    return ""
