"""Tests for temporary static report scoring."""

from uuid import uuid4

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue
from app.services.report_generation_service import build_static_report, calculate_score


def test_calculate_score_uses_weighted_severity_penalties() -> None:
    issues = [
        _issue(IssueSeverity.CRITICAL, IssueCategory.SECURITY, "a.py"),
        _issue(IssueSeverity.LOW, IssueCategory.STYLE, "b.py"),
    ]

    assert calculate_score(issues) == 6.7


def test_build_static_report_counts_scores_and_risky_files() -> None:
    job_id = uuid4()
    issues = [
        _issue(IssueSeverity.CRITICAL, IssueCategory.SECURITY, "a.py"),
        _issue(IssueSeverity.HIGH, IssueCategory.BUG, "a.py"),
        _issue(IssueSeverity.LOW, IssueCategory.STYLE, "b.py"),
    ]

    report = build_static_report(
        job_id=job_id,
        total_files_analyzed=4,
        issues=issues,
        tech_stack={"languages": {"python": 1}},
    )

    assert report.job_id == job_id
    assert report.total_files_analyzed == 4
    assert report.critical_count == 1
    assert report.high_count == 1
    assert report.overall_score == 4.7
    assert report.security_score == 7.0
    assert report.top_risky_files is not None
    assert report.top_risky_files[0]["path"] == "a.py"


def _issue(
    severity: IssueSeverity,
    category: IssueCategory,
    file_path: str,
) -> NormalizedIssue:
    return NormalizedIssue(
        file_path=file_path,
        line_start=1,
        line_end=1,
        severity=severity,
        category=category,
        title="Finding",
        description="Finding description",
        source=IssueSource.RUFF,
        confidence=0.9,
    )
