"""Tests for report issue response enrichment."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.services.reporting.issue_presenter import (
    _group_issues,
    _issue_group_key,
    _issue_group_response,
    _source_context_from_chunk,
)
from app.services.reporting.service import ReportService


@pytest.mark.asyncio
async def test_refresh_report_aggregates_clears_deprecated_scores() -> None:
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
    assert report.security_score is None
    assert report.maintainability_score is None
    assert report.performance_score is None
    assert report.overall_score is None


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


def test_issue_group_key_prefers_static_rule_id() -> None:
    job_id = uuid4()
    first_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Hardcoded secret in settings",
        raw_output={"test_id": "B105"},
    )
    second_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Hardcoded password literal",
        raw_output={"test_id": "B105"},
    )

    assert _issue_group_key(cast(ReviewIssue, first_issue)) == _issue_group_key(
        cast(ReviewIssue, second_issue)
    )


def test_issue_group_key_prefers_semantic_finding_key_for_ai_issues() -> None:
    job_id = uuid4()
    first_issue = _issue(
        job_id,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        title="Password is written to logs",
        raw_output={"finding_key": "security:sensitive_data_in_logs"},
    )
    second_issue = _issue(
        job_id,
        IssueSeverity.MEDIUM,
        IssueCategory.SECURITY,
        title="Login diagnostics expose a credential",
        raw_output={"finding_key": "security:sensitive_data_logging"},
    )

    assert _issue_group_key(cast(ReviewIssue, first_issue)) == _issue_group_key(
        cast(ReviewIssue, second_issue)
    )


def test_issue_group_response_counts_occurrences_and_files() -> None:
    job_id = uuid4()
    issues = [
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.SECURITY,
            file_path="app/auth.py",
            description="Hardcoded password literal in auth flow",
            suggestion="Move the password to a secret manager.",
            raw_output={"test_id": "B105"},
        ),
        _issue(
            job_id,
            IssueSeverity.HIGH,
            IssueCategory.SECURITY,
            file_path="app/settings.py",
            description="Hardcoded secret-like value in settings",
            suggestion="Read the setting from an environment variable.",
            raw_output={"test_id": "B105"},
        ),
    ]
    groups = _group_issues(cast(list[ReviewIssue], issues))
    group_key, grouped_issues = next(iter(groups.items()))

    response = _issue_group_response(
        group_key,
        grouped_issues,
        include_occurrences=True,
    )

    assert response.occurrence_count == 2
    assert response.affected_files == ["app/auth.py", "app/settings.py"]
    assert [occurrence.file_path for occurrence in response.occurrences] == [
        "app/auth.py",
        "app/settings.py",
    ]
    assert [occurrence.description for occurrence in response.occurrences] == [
        "Hardcoded password literal in auth flow",
        "Hardcoded secret-like value in settings",
    ]
    assert [occurrence.suggestion for occurrence in response.occurrences] == [
        "Move the password to a secret manager.",
        "Read the setting from an environment variable.",
    ]


class _IssueRepository:
    def __init__(self, issues: list[SimpleNamespace]) -> None:
        self.issues = issues

    async def list_all_issues(self, job_id: object) -> list[SimpleNamespace]:
        return [issue for issue in self.issues if issue.job_id == job_id]

    async def save_report(self, report: object) -> object:
        return report


def _issue(
    job_id: object,
    severity: IssueSeverity,
    category: IssueCategory,
    *,
    file_path: str | None = None,
    raw_output: dict[str, object] | None = None,
    title: str = "Finding",
    description: str = "Finding description",
    suggestion: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        job_id=job_id,
        file_path=file_path or f"{category.value}.py",
        line_start=1,
        line_end=1,
        severity=severity,
        category=category,
        title=title,
        description=description,
        suggestion=suggestion,
        source=IssueSource.AI_REVIEW,
        confidence=0.9,
        raw_output=raw_output,
        created_at=datetime.now(UTC),
    )
