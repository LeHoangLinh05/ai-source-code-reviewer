"""Tests for static report scoring."""

from uuid import uuid4

from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue
from app.services.reporting.generation import (
    build_static_report,
    build_top_risky_files,
    calculate_report_scores,
    calculate_score,
)


def test_calculate_score_uses_weighted_severity_penalties() -> None:
    issues = [
        _issue(IssueSeverity.CRITICAL, IssueCategory.SECURITY, "a.py"),
        _issue(IssueSeverity.LOW, IssueCategory.STYLE, "b.py"),
    ]

    assert calculate_score(issues) == 7.2


def test_calculate_report_scores_uses_all_persisted_issue_categories() -> None:
    issues = [
        _issue(IssueSeverity.CRITICAL, IssueCategory.SECURITY, "security.py"),
        _issue(IssueSeverity.HIGH, IssueCategory.BUG, "bug.py"),
        _issue(IssueSeverity.LOW, IssueCategory.REQUIREMENT, "requirements.py"),
        _issue(IssueSeverity.LOW, IssueCategory.PERFORMANCE, "perf.py"),
    ]

    assert calculate_report_scores(issues) == {
        "security_score": 7.4,
        "maintainability_score": 8.2,
        "performance_score": 9.7,
        "overall_score": 5.7,
    }


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
    assert report.overall_score == 5.9
    assert report.security_score == 7.4
    assert report.top_risky_files is not None
    assert report.top_risky_files[0]["path"] == "a.py"


def test_top_risky_files_prioritize_p0_then_categories() -> None:
    issues = [
        _issue(IssueSeverity.HIGH, IssueCategory.MAINTAINABILITY, "maint.py"),
        _issue(IssueSeverity.HIGH, IssueCategory.PERFORMANCE, "perf.py"),
        _issue(IssueSeverity.HIGH, IssueCategory.BUG, "bug.py"),
        _issue(IssueSeverity.HIGH, IssueCategory.SECURITY, "security.py"),
        NormalizedIssue(
            file_path="requirements.py",
            line_start=1,
            line_end=1,
            severity=IssueSeverity.CRITICAL,
            category=IssueCategory.REQUIREMENT,
            title="Missing required behavior",
            description="Missing required behavior",
            source=IssueSource.KB,
            confidence=0.9,
            raw_output={"priority": "P0"},
        ),
    ]

    assert [item["path"] for item in build_top_risky_files(issues)] == [
        "requirements.py",
        "security.py",
        "bug.py",
        "perf.py",
        "maint.py",
    ]


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
