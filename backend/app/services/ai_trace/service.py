"""Lightweight AI trace aggregation for review job visibility."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from app.ai.review.plan import (
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)
from app.ai.review.source_evidence import SOURCE_TOOL_NAMES
from app.models.review_issue import IssueSource
from app.models.review_job import ReviewJob
from app.models.review_report import ReviewReport
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    ToolCallLogRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.ai_trace import (
    AIToolCallTrace,
    AITraceCoverage,
    AITraceResponse,
)
from app.services.ai_trace.coverage import (
    _broad_audited_chunk_count,
    _percent,
    _probe_slot_counts,
    _read_chunk_coverage,
    _safe_int,
    _structure_coverage,
)
from app.services.ai_trace.events import _token_totals, to_tool_call_trace
from app.services.ai_trace.stages import _is_full_ai_review, build_trace_stages


class AITraceService:
    """Build a compact execution trace from tool logs and persisted results."""

    def __init__(
        self,
        *,
        review_job_repository: ReviewJobRepository,
        report_repository: ReportRepository,
        file_analysis_repository: FileAnalysisResultRepository,
        raw_static_repository: RawStaticAnalysisOutputRepository,
        tool_call_repository: ToolCallLogRepository,
        chunk_metadata_repository: ChunkMetadataRepository,
    ) -> None:
        self.review_job_repository = review_job_repository
        self.report_repository = report_repository
        self.file_analysis_repository = file_analysis_repository
        self.raw_static_repository = raw_static_repository
        self.tool_call_repository = tool_call_repository
        self.chunk_metadata_repository = chunk_metadata_repository

    async def get_trace(self, job_id: UUID) -> AITraceResponse:
        """Return the latest AI trace snapshot for a review job."""

        tool_call_count = await self.tool_call_repository.count_tool_events(job_id)
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
            stages=build_trace_stages(
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
        documents = cast(
            list[dict[str, Any]],
            await self.tool_call_repository.find_trace_events(job_id),
        )
        return [to_tool_call_trace(document) for document in documents]

    async def _load_issue_counts(self, job_id: UUID) -> dict[str, int]:
        counts = await self.report_repository.count_issues_by_source(job_id)
        return {source.value: count for source, count in counts.items()}

    async def _load_job(self, job_id: UUID) -> ReviewJob | None:
        return await self.review_job_repository.get_by_id(job_id)

    async def _load_report(self, job_id: UUID) -> ReviewReport | None:
        return await self.report_repository.get_report_by_job_id(job_id)

    async def _load_coverage(
        self,
        *,
        job_id: UUID,
        issue_counts: dict[str, int],
        job: ReviewJob | None,
        report: ReviewReport | None,
    ) -> AITraceCoverage:
        structure_document = await self.file_analysis_repository.find_latest_by_job_id(
            job_id
        )
        static_documents = cast(
            list[dict[str, Any]],
            await self.raw_static_repository.find_by_job_id(job_id),
        )
        read_chunk_documents = cast(
            list[dict[str, Any]],
            await self.tool_call_repository.find_source_reads(
                job_id=job_id,
                tool_names=SOURCE_TOOL_NAMES,
            ),
        )
        chunk_documents = cast(
            list[dict[str, Any]],
            await self.chunk_metadata_repository.find_by_job_id(job_id),
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
        broad_audited_chunks = _broad_audited_chunk_count(read_chunk_documents)
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
            broad_audited_chunks=broad_audited_chunks,
            broad_audit_chunk_percent=_percent(broad_audited_chunks, total_chunks),
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
