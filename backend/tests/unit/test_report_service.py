"""Tests for report issue response enrichment."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.services.report_service import ReportService
from app.services.report_service import _source_context_from_chunk


@pytest.mark.asyncio
async def test_refresh_report_aggregates_recalculates_scores_from_all_issues() -> None:
    job_id = uuid4()
    report = SimpleNamespace(
        job_id=job_id,
        total_issues=0,
        critical_count=0,
        high_count=0,
        medium_count=0,
        low_count=0,
        info_count=0,
        security_score=0.0,
        maintainability_score=0.0,
        performance_score=0.0,
        overall_score=0.0,
        top_risky_files=[],
    )
    service = ReportService(
        report_repository=_IssueRepository(
            [
                _issue(job_id, IssueSeverity.CRITICAL, IssueCategory.SECURITY),
                _issue(job_id, IssueSeverity.HIGH, IssueCategory.BUG),
                _issue(job_id, IssueSeverity.LOW, IssueCategory.REQUIREMENT),
            ]
        ),  # type: ignore[arg-type]
        chunk_metadata_repository=object(),  # type: ignore[arg-type]
    )

    await service._refresh_report_aggregates(report)  # type: ignore[arg-type]

    assert report.total_issues == 3
    assert report.critical_count == 1
    assert report.high_count == 1
    assert report.low_count == 1
    assert report.security_score == 7.0
    assert report.maintainability_score == 8.0
    assert report.performance_score == 10.0
    assert report.overall_score == 4.7


def test_source_context_falls_back_to_persisted_chunk() -> None:
    source_context = _source_context_from_chunk(
        {
            "line_start": 10,
            "line_end": 14,
            "chunk_text": "ten\neleven\nproblem\nthirteen\nfourteen\n",
        },
        line_start=12,
        line_end=12,
        context_radius=1,
    )

    assert source_context == {
        "start_line": 11,
        "lines": ["eleven", "problem", "thirteen"],
    }


class _IssueRepository:
    def __init__(self, issues: list[SimpleNamespace]) -> None:
        self.issues = issues

    async def list_all_issues(self, job_id: object) -> list[SimpleNamespace]:
        return [issue for issue in self.issues if issue.job_id == job_id]


def _issue(
    job_id: object,
    severity: IssueSeverity,
    category: IssueCategory,
) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=job_id,
        file_path=f"{category.value}.py",
        line_start=1,
        line_end=1,
        severity=severity,
        category=category,
        title="Finding",
        description="Finding description",
        suggestion=None,
        source=IssueSource.AI_REVIEW,
        confidence=0.9,
        raw_output=None,
    )
