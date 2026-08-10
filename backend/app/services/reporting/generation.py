"""Deterministic static-analysis report generation."""

from collections import Counter
from uuid import UUID

from app.models.review_issue import IssueCategory, IssueSeverity
from app.models.review_report import ReviewReport
from app.schemas.normalized_issue import NormalizedIssue

STATIC_REPORT_MODEL = "static-pipeline-v1"
AI_REPORT_MODEL = "langchain-structured-report-v1"

SEVERITY_RANK = {
    IssueSeverity.CRITICAL: 5,
    IssueSeverity.HIGH: 4,
    IssueSeverity.MEDIUM: 3,
    IssueSeverity.LOW: 2,
    IssueSeverity.INFO: 1,
}


def build_static_report(
    *,
    job_id: UUID,
    total_files_analyzed: int,
    issues: list[NormalizedIssue],
    tech_stack: dict[str, object],
) -> ReviewReport:
    """Build a static report from normalized analyzer findings."""

    severity_counts = Counter(issue.severity for issue in issues)
    return ReviewReport(
        job_id=job_id,
        total_files_analyzed=total_files_analyzed,
        total_issues=len(issues),
        critical_count=severity_counts[IssueSeverity.CRITICAL],
        high_count=severity_counts[IssueSeverity.HIGH],
        medium_count=severity_counts[IssueSeverity.MEDIUM],
        low_count=severity_counts[IssueSeverity.LOW],
        info_count=severity_counts[IssueSeverity.INFO],
        security_score=None,
        maintainability_score=None,
        performance_score=None,
        overall_score=None,
        tech_stack=tech_stack,
        top_risky_files=build_top_risky_files(issues),
        executive_summary=build_executive_summary(issues, total_files_analyzed),
        ai_model_used=STATIC_REPORT_MODEL,
    )


def build_top_risky_files(
    issues: list[NormalizedIssue],
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Rank files by issue count and highest severity."""

    grouped: dict[str, list[NormalizedIssue]] = {}
    for issue in issues:
        file_path = issue.file_path or "Unknown file"
        grouped.setdefault(file_path, []).append(issue)

    ranked_files = sorted(
        grouped.items(),
        key=lambda item: (
            min(_report_priority(issue) for issue in item[1]),
            -max(SEVERITY_RANK[issue.severity] for issue in item[1]),
            -len(item[1]),
        ),
    )
    return [
        {
            "path": file_path,
            "issue_count": len(file_issues),
            "max_severity": max(
                file_issues,
                key=lambda issue: SEVERITY_RANK[issue.severity],
            ).severity.value,
        }
        for file_path, file_issues in ranked_files[:limit]
    ]


def _report_priority(issue: NormalizedIssue) -> int:
    category_priorities = {
        IssueCategory.SECURITY: 1,
        IssueCategory.BUG: 2,
        IssueCategory.PERFORMANCE: 3,
        IssueCategory.MAINTAINABILITY: 4,
        IssueCategory.REQUIREMENT: 5,
    }
    if (issue.raw_output or {}).get("priority") == "P0":
        return 0

    return category_priorities.get(issue.category, 6)


def build_executive_summary(
    issues: list[NormalizedIssue],
    total_files_analyzed: int,
) -> str:
    """Create a short static-analysis summary for the report screen."""

    if not issues:
        return (
            f"Static pipeline analyzed {total_files_analyzed} files and found no "
            "Ruff, Bandit, ESLint, or secret scanner findings."
        )

    severity_counts = Counter(issue.severity for issue in issues)
    return (
        f"Static pipeline analyzed {total_files_analyzed} files and found "
        f"{len(issues)} issues: {severity_counts[IssueSeverity.CRITICAL]} critical, "
        f"{severity_counts[IssueSeverity.HIGH]} high, "
        f"{severity_counts[IssueSeverity.MEDIUM]} medium, "
        f"{severity_counts[IssueSeverity.LOW]} low, and "
        f"{severity_counts[IssueSeverity.INFO]} informational."
    )
