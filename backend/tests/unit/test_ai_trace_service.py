"""Tests for AI trace coverage/status helpers."""

from app.models.review_job import ReviewJobStatus
from app.schemas.ai_trace import AITraceCoverage
from app.services.ai_trace_service import (
    _ai_stage_status,
    _read_chunk_coverage,
    _report_stage_status,
)
from app.services.report_generation_service import AI_REPORT_MODEL


def test_read_chunk_coverage_counts_only_successful_unique_chunks() -> None:
    files_read, chunks_read, target_chunks_read = _read_chunk_coverage(
        [
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "ok",
                    "file_path": "app/main.py",
                    "chunk_index": 0,
                },
            },
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "rejected",
                    "file_path": "app/main.py",
                    "chunk_index": 1,
                },
            },
            {
                "tool_name": "read_file_chunk",
                "output": {
                    "status": "ok",
                    "file_path": "app/main.py",
                    "chunk_index": 0,
                },
            },
            {
                "tool_name": "search_code_semantic",
                "output": {
                    "status": "ok",
                    "results": [
                        {
                            "status": "ok",
                            "file_path": "app/main.py",
                            "chunk_index": 1,
                            "line_start": 10,
                            "line_end": 20,
                        }
                    ],
                },
            },
        ],
        target_chunk_keys={("app/main.py", 0), ("app/main.py", 1)},
    )

    assert files_read == 1
    assert chunks_read == 2
    assert target_chunks_read == 2


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
        roadmap_rules_checked=79,
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
