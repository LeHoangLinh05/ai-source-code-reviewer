"""Tests for AI trace coverage/status helpers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.models.review_issue import IssueSource
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    ToolCallLogRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.ai_trace import AIToolCallTrace, AITraceCoverage
from app.services.ai_trace.coverage import (
    _broad_audited_chunk_count,
    _probe_slot_counts,
    _read_chunk_coverage,
)
from app.services.ai_trace.events import _token_totals, _token_usage_dict
from app.services.ai_trace.service import AITraceService
from app.services.ai_trace.stages import _ai_stage_status, _report_stage_status
from app.services.reporting.generation import AI_REPORT_MODEL


@pytest.mark.asyncio
async def test_ai_trace_service_delegates_postgres_reads_to_repositories() -> None:
    job_id = uuid4()
    review_job = cast(ReviewJob, SimpleNamespace(id=job_id))
    report = cast(ReviewReport, SimpleNamespace(job_id=job_id))

    class ReviewJobRepositoryStub:
        async def get_by_id(self, requested_job_id: UUID) -> ReviewJob:
            assert requested_job_id == job_id
            return review_job

    class ReportRepositoryStub:
        async def count_issues_by_source(
            self,
            requested_job_id: UUID,
        ) -> dict[IssueSource, int]:
            assert requested_job_id == job_id
            return {IssueSource.AI_REVIEW: 2}

        async def get_report_by_job_id(
            self,
            requested_job_id: UUID,
        ) -> ReviewReport:
            assert requested_job_id == job_id
            return report

    service = AITraceService(
        review_job_repository=cast(
            ReviewJobRepository,
            ReviewJobRepositoryStub(),
        ),
        report_repository=cast(ReportRepository, ReportRepositoryStub()),
        file_analysis_repository=cast(FileAnalysisResultRepository, object()),
        raw_static_repository=cast(RawStaticAnalysisOutputRepository, object()),
        tool_call_repository=cast(ToolCallLogRepository, object()),
        chunk_metadata_repository=cast(ChunkMetadataRepository, object()),
    )

    assert await service._load_issue_counts(job_id) == {"ai_review": 2}
    assert await service._load_job(job_id) is review_job
    assert await service._load_report(job_id) is report


def test_read_chunk_coverage_counts_only_successful_unique_chunks() -> None:
    files_read, chunks_read, target_chunks_read = _read_chunk_coverage(
        [
            {
                "tool_name": "probe_retrieval",
                "output": {
                    "status": "ok",
                    "results": [
                        {
                            "file_path": "app/main.py",
                            "chunk_index": 0,
                        },
                        {
                            "file_path": "app/main.py",
                            "chunk_index": 0,
                        },
                        {
                            "status": "ok",
                            "file_path": "app/main.py",
                            "chunk_index": 1,
                            "line_start": 10,
                            "line_end": 20,
                        },
                    ],
                },
            },
        ],
        target_chunk_keys={("app/main.py", 0), ("app/main.py", 1)},
    )

    assert files_read == 1
    assert chunks_read == 2
    assert target_chunks_read == 2


def test_probe_slot_counts_distinguish_retrieved_from_judged() -> None:
    retrieved, judged = _probe_slot_counts(
        [
            {
                "tool_name": "probe_retrieval",
                "output": {
                    "status": "ok",
                    "selected_count": 6,
                    "trimmed_count": 3,
                    "sent_to_judge": 3,
                    "result_count": 3,
                },
            },
            {
                "tool_name": "probe_retrieval",
                "output": {
                    "status": "ok",
                    "result_count": 2,
                },
            },
        ]
    )

    assert retrieved == 8
    assert judged == 5


def test_broad_audit_coverage_counts_unique_coverage_lane_chunks() -> None:
    count = _broad_audited_chunk_count(
        [
            {
                "tool_name": "probe_retrieval",
                "input": {"lane": "defect"},
                "output": {
                    "results": [
                        {"file_path": "app/auth.py", "chunk_index": 0},
                    ]
                },
            },
            {
                "tool_name": "probe_retrieval",
                "input": {"lane": "coverage"},
                "output": {
                    "results": [
                        {"file_path": "app/auth.py", "chunk_index": 0},
                        {"file_path": "app/files.py", "chunk_index": 1},
                        {"file_path": "app/files.py", "chunk_index": 1},
                    ]
                },
            },
        ]
    )

    assert count == 2


def test_ai_report_without_full_chunk_coverage_is_completed() -> None:
    coverage = AITraceCoverage(
        total_reviewable_files=3,
        total_reviewable_lines=30,
        review_mode="smart",
        chunked_files=3,
        total_chunks=10,
        target_files=3,
        target_chunks=6,
        ai_read_files=2,
        ai_read_chunks=4,
        ai_read_target_chunks=4,
        ai_read_file_percent=66.7,
        ai_read_chunk_percent=66.7,
        static_analyzer_runs=3,
        static_analyzer_issues=0,
        generated_ai_issues=0,
        generated_report_by_ai=False,
    )

    assert (
        _ai_stage_status(
            status=ReviewJobStatus.COMPLETED,
            has_ai_started=True,
            coverage=coverage,
            report_model=AI_REPORT_MODEL,
        )
        == "completed"
    )
    assert _report_stage_status(
        ReviewJobStatus.COMPLETED, coverage, AI_REPORT_MODEL
    ) == ("completed")


def test_trace_token_totals_include_llm_and_embedding_estimates() -> None:
    events = [
        AIToolCallTrace(
            sequence=1,
            tool_name="llm_call",
            called_at=datetime.now(UTC),
            duration_ms=10,
            input={},
            output={},
            status="ok",
            event_type="llm",
            token_usage={
                "input_tokens": 100,
                "output_tokens": 40,
                "total_tokens": 140,
            },
        ),
        AIToolCallTrace(
            sequence=2,
            tool_name="code_embedding_batch",
            called_at=datetime.now(UTC),
            duration_ms=5,
            input={},
            output={},
            status="ok",
            event_type="embedding",
            token_usage={"estimated_input_tokens": 75},
        ),
    ]

    totals = _token_totals(events)

    assert totals.input_tokens == 100
    assert totals.output_tokens == 40
    assert totals.total_tokens == 140
    assert totals.estimated_input_tokens == 75


def test_token_usage_dict_drops_unknown_fields() -> None:
    assert _token_usage_dict(
        {
            "input_tokens": 12,
            "prompt_tokens": 99,
            "estimated_input_tokens": 7,
            "bad": "value",
        }
    ) == {
        "input_tokens": 12,
        "estimated_input_tokens": 7,
    }
