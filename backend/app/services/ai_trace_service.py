"""Lightweight AI trace aggregation for review job visibility."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.review_plan import (
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)
from app.ai.source_evidence import SOURCE_TOOL_NAMES, source_chunk_keys
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
    TOOL_CALL_LOGS_COLLECTION,
)
from app.models.review_issue import IssueSource, ReviewIssue
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.schemas.ai_trace import (
    AITokenTotals,
    AIToolCallTrace,
    AITraceCoverage,
    AITraceResponse,
    AITraceStage,
)
from app.services.report_generation_service import AI_REPORT_MODEL, STATIC_REPORT_MODEL

MAX_TEXT_PREVIEW = 700
MAX_LIST_PREVIEW_ITEMS = 6
MAX_DICT_PREVIEW_ITEMS = 10


class AITraceService:
    """Build a compact execution trace from tool logs and persisted results."""

    def __init__(
        self,
        *,
        postgres_session: AsyncSession,
        mongodb_database: AsyncIOMotorDatabase,
    ) -> None:
        self.postgres_session = postgres_session
        self.mongodb_database = mongodb_database

    async def get_trace(self, job_id: UUID) -> AITraceResponse:
        """Return the latest AI trace snapshot for a review job."""

        tool_filter = {
            "job_id": str(job_id),
            "$or": [{"event_type": "tool"}, {"event_type": {"$exists": False}}],
        }
        tool_call_count = await self.mongodb_database[
            TOOL_CALL_LOGS_COLLECTION
        ].count_documents(tool_filter)
        events = await self._load_trace_events(job_id)
        tool_calls = [event for event in events if event.event_type == "tool"]
        issue_counts = await self._load_issue_counts(job_id)
        job = await self._load_job(job_id)
        report = await self._load_report(job_id)
        coverage = await self._load_coverage(
            job_id=job_id,
            issue_counts=issue_counts,
            job=job,
            report=report,
        )
        latest_tool_call = tool_calls[-1] if tool_calls else None

        return AITraceResponse(
            job_id=job_id,
            has_ai_started=tool_call_count > 0,
            tool_call_count=tool_call_count,
            latest_tool_name=latest_tool_call.tool_name if latest_tool_call else None,
            latest_tool_status=latest_tool_call.status if latest_tool_call else None,
            issue_counts_by_source=issue_counts,
            ai_issue_count=(
                issue_counts.get(IssueSource.AI_REVIEW.value, 0)
                + issue_counts.get(IssueSource.KB.value, 0)
            ),
            static_issue_count=sum(
                count
                for source, count in issue_counts.items()
                if source not in {IssueSource.AI_REVIEW.value, IssueSource.KB.value}
            ),
            report_model=report.ai_model_used if report else None,
            report_created_at=report.created_at if report else None,
            coverage=coverage,
            stages=self._build_stages(
                job=job,
                coverage=coverage,
                has_ai_started=tool_call_count > 0,
                latest_tool_call=latest_tool_call,
                report=report,
            ),
            recent_tool_calls=tool_calls,
            events=events,
            token_totals=_token_totals(events),
        )

    async def _load_trace_events(self, job_id: UUID) -> list[AIToolCallTrace]:
        cursor = (
            self.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
            .find({"job_id": str(job_id)})
            .sort([("called_at", 1), ("sequence", 1)])
        )
        documents = cast(list[dict[str, Any]], await cursor.to_list(length=None))
        return [self._to_tool_call_trace(document) for document in documents]

    async def _load_issue_counts(self, job_id: UUID) -> dict[str, int]:
        result = await self.postgres_session.execute(
            select(ReviewIssue.source, func.count(ReviewIssue.id))
            .where(ReviewIssue.job_id == job_id)
            .group_by(ReviewIssue.source)
        )
        return {str(source.value): int(count) for source, count in result.all()}

    async def _load_job(self, job_id: UUID) -> ReviewJob | None:
        result = await self.postgres_session.execute(
            select(ReviewJob).where(ReviewJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def _load_report(self, job_id: UUID) -> ReviewReport | None:
        result = await self.postgres_session.execute(
            select(ReviewReport).where(ReviewReport.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def _load_coverage(
        self,
        *,
        job_id: UUID,
        issue_counts: dict[str, int],
        job: ReviewJob | None,
        report: ReviewReport | None,
    ) -> AITraceCoverage:
        job_filter = {"job_id": str(job_id)}
        structure_document = await self.mongodb_database[
            FILE_ANALYSIS_RESULTS_COLLECTION
        ].find_one(job_filter, sort=[("analyzed_at", -1)])
        static_documents = cast(
            list[dict[str, Any]],
            await self.mongodb_database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION]
            .find(job_filter)
            .to_list(length=None),
        )
        read_chunk_documents = cast(
            list[dict[str, Any]],
            await self.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
            .find(
                {
                    **job_filter,
                    "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)},
                }
            )
            .to_list(length=None),
        )
        chunk_documents = cast(
            list[dict[str, Any]],
            await self.mongodb_database[CHUNK_METADATA_COLLECTION]
            .find(job_filter)
            .to_list(length=None),
        )

        total_reviewable_files, total_reviewable_lines = _structure_coverage(
            structure_document
        )
        total_chunks = len(chunk_documents)
        chunked_files = len(
            {
                str(document.get("file_path"))
                for document in chunk_documents
                if isinstance(document.get("file_path"), str)
            }
        )
        review_mode = get_review_mode(job.options if job else None)
        review_plan = build_chunk_review_plan(
            chunk_documents=chunk_documents,
            review_mode=review_mode,
            max_smart_chunks=get_smart_review_max_chunks(job.options if job else None),
        )
        target_chunks = _safe_int(review_plan.get("target_chunks"))
        target_files = _safe_int(review_plan.get("target_files"))
        target_chunk_keys = expected_chunk_keys_from_plan(review_plan)
        ai_read_files, ai_read_chunks, ai_read_target_chunks = _read_chunk_coverage(
            read_chunk_documents,
            target_chunk_keys=target_chunk_keys,
        )
        ai_retrieved_chunks, ai_judged_chunks = _probe_slot_counts(read_chunk_documents)
        static_analyzer_issues = sum(
            len(document.get("parsed_issues", []))
            for document in static_documents
            if isinstance(document.get("parsed_issues", []), list)
        )

        return AITraceCoverage(
            total_reviewable_files=total_reviewable_files,
            total_reviewable_lines=total_reviewable_lines,
            review_mode=review_mode,
            chunked_files=chunked_files,
            total_chunks=total_chunks,
            target_files=target_files,
            target_chunks=target_chunks,
            ai_read_files=ai_read_files,
            ai_read_chunks=ai_read_chunks,
            ai_retrieved_chunks=ai_retrieved_chunks,
            ai_judged_chunks=ai_judged_chunks,
            ai_read_target_chunks=ai_read_target_chunks,
            ai_read_file_percent=_percent(ai_read_files, max(target_files, 1)),
            ai_read_chunk_percent=_percent(ai_read_target_chunks, target_chunks),
            static_analyzer_runs=len(static_documents),
            static_analyzer_issues=static_analyzer_issues,
            generated_ai_issues=(
                issue_counts.get(IssueSource.AI_REVIEW.value, 0)
                + issue_counts.get(IssueSource.KB.value, 0)
            ),
            generated_report_by_ai=_is_full_ai_review(
                report_model=report.ai_model_used if report else None,
                coverage_ai_chunks=ai_read_target_chunks,
                total_chunks=target_chunks,
            ),
        )

    def _build_stages(
        self,
        *,
        job: ReviewJob | None,
        coverage: AITraceCoverage,
        has_ai_started: bool,
        latest_tool_call: AIToolCallTrace | None,
        report: ReviewReport | None,
    ) -> list[AITraceStage]:
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
                detail=job.commit_sha[:7]
                if job and job.commit_sha
                else "Waiting for repository clone",
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

    def _to_tool_call_trace(self, document: dict[str, Any]) -> AIToolCallTrace:
        output = _preview_value(document.get("output", {}))
        output_dict = output if isinstance(output, dict) else {"output": output}
        tool_name = str(document.get("tool_name", "unknown"))
        tool_input = _preview_dict(document.get("input", {}))
        return AIToolCallTrace(
            sequence=int(document.get("sequence", 0)),
            tool_name=tool_name,
            called_at=document["called_at"],
            duration_ms=int(document.get("duration_ms", 0)),
            input=tool_input,
            output=output_dict,
            status=str(
                document.get("status") or _tool_call_status(tool_name, output_dict)
            ),
            event_type=str(document.get("event_type") or "tool"),
            provider=_optional_str(document.get("provider")),
            model=_optional_str(document.get("model")),
            phase=_optional_str(document.get("phase")),
            token_usage=_token_usage_dict(document.get("token_usage")),
            metadata=_preview_dict(document.get("metadata", {})),
        )


def _token_totals(events: list[AIToolCallTrace]) -> AITokenTotals:
    totals = AITokenTotals()
    for event in events:
        usage = event.token_usage or {}
        totals.input_tokens += _safe_int(usage.get("input_tokens"))
        totals.output_tokens += _safe_int(usage.get("output_tokens"))
        totals.total_tokens += _safe_int(usage.get("total_tokens"))
        totals.estimated_input_tokens += _safe_int(usage.get("estimated_input_tokens"))
    return totals


def _token_usage_dict(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    usage: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_input_tokens",
    ):
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            usage[key] = item
    return usage or None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _preview_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"value": _preview_value(value)}

    preview: dict[str, object] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_DICT_PREVIEW_ITEMS:
            preview["..."] = f"{len(value) - MAX_DICT_PREVIEW_ITEMS} more fields"
            break
        preview[str(key)] = _preview_value(item)

    return preview


def _preview_value(value: object) -> object:
    if isinstance(value, str):
        return _preview_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        items = [_preview_value(item) for item in value[:MAX_LIST_PREVIEW_ITEMS]]
        if len(value) > MAX_LIST_PREVIEW_ITEMS:
            items.append(f"... {len(value) - MAX_LIST_PREVIEW_ITEMS} more items")
        return items
    if isinstance(value, dict):
        return _preview_dict(value)

    return _preview_text(str(value))


def _preview_text(value: str) -> str:
    if len(value) <= MAX_TEXT_PREVIEW:
        return value

    return f"{value[:MAX_TEXT_PREVIEW]}..."


def _structure_coverage(document: dict[str, Any] | None) -> tuple[int, int]:
    if document is None:
        return 0, 0

    file_tree = document.get("file_tree", [])
    if not isinstance(file_tree, list):
        return 0, 0

    reviewable_entries = [
        entry
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    ]
    total_lines = sum(
        _safe_int(entry.get("line_count")) for entry in reviewable_entries
    )
    return len(reviewable_entries), total_lines


def _read_chunk_coverage(
    documents: list[dict[str, Any]],
    *,
    target_chunk_keys: set[tuple[str, int]] | None = None,
) -> tuple[int, int, int]:
    read_chunks = source_chunk_keys(documents)
    read_files = {file_path for file_path, _chunk_index in read_chunks}

    if target_chunk_keys is None:
        read_target_chunks = len(read_chunks)
    else:
        read_target_chunks = len(read_chunks & target_chunk_keys)

    return len(read_files), len(read_chunks), read_target_chunks


def _probe_slot_counts(documents: list[dict[str, Any]]) -> tuple[int, int]:
    retrieved_count = 0
    judged_count = 0
    for document in documents:
        if document.get("tool_name") != "probe_retrieval":
            continue
        output = document.get("output")
        if not isinstance(output, dict):
            continue
        result_count = _safe_int(output.get("result_count"))
        retrieved_count += _safe_int(output.get("selected_count")) or result_count
        judged_count += _safe_int(output.get("sent_to_judge")) or result_count
    return retrieved_count, judged_count


def _percent(current: int, total: int) -> float:
    if total <= 0:
        return 0.0

    return round(min(100.0, (current / total) * 100.0), 1)


def _tool_call_status(tool_name: str, output: dict[str, object]) -> str:
    if tool_name == "_Exception":
        return "error"

    if "error" in output:
        return "error"

    status = output.get("status")
    if isinstance(status, str) and status:
        return status

    return "ok"


def _is_full_ai_review(
    *,
    report_model: str | None,
    coverage_ai_chunks: int,
    total_chunks: int,
) -> bool:
    _ = coverage_ai_chunks, total_chunks
    return report_model == AI_REPORT_MODEL


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) else 0


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
    if report_model == AI_REPORT_MODEL:
        return "warning"
    if report_model == STATIC_REPORT_MODEL:
        return "warning"
    if current_status == ReviewJobStatus.GENERATING_REPORT:
        return "running"
    if current_status == ReviewJobStatus.FAILED:
        return "failed"
    return "pending"


def _report_stage_detail(coverage: AITraceCoverage, report_model: str | None) -> str:
    if _is_full_ai_review(
        report_model=report_model,
        coverage_ai_chunks=coverage.ai_read_target_chunks,
        total_chunks=coverage.target_chunks,
    ):
        return "Final report was generated by the AI agent"
    if report_model == AI_REPORT_MODEL:
        return "Final report was generated by the AI agent"
    if report_model == STATIC_REPORT_MODEL:
        return "Waiting for the AI final report"
    return ""
