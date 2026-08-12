"""Canonical findings, source occurrences, and report aggregates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.review_targets import is_review_target_path
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.services.reporting.finding_identity import (
    normalize_finding_path,
    review_issue_finding_key,
)

SEVERITY_SORT_ORDER = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.HIGH: 1,
    IssueSeverity.MEDIUM: 2,
    IssueSeverity.LOW: 3,
    IssueSeverity.INFO: 4,
}

SOURCE_SORT_ORDER = {
    IssueSource.KB: 0,
    IssueSource.AI_REVIEW: 1,
    IssueSource.SECRET_SCANNER: 2,
    IssueSource.BANDIT: 3,
    IssueSource.ESLINT: 4,
    IssueSource.RUFF: 5,
}


@dataclass(slots=True, frozen=True)
class CanonicalOccurrence:
    """One canonical claim at one overlapping source range."""

    representative: ReviewIssue
    issues: tuple[ReviewIssue, ...]


@dataclass(slots=True, frozen=True)
class CanonicalFinding:
    """One canonical claim family with all source occurrences."""

    finding_key: str
    representative: ReviewIssue
    occurrences: tuple[CanonicalOccurrence, ...]
    raw_issues: tuple[ReviewIssue, ...]


@dataclass(slots=True, frozen=True)
class ReportAggregate:
    """Deterministic counts and representatives for one review report."""

    findings: tuple[CanonicalFinding, ...]
    occurrences: tuple[CanonicalOccurrence, ...]
    raw_issue_count: int
    severity_counts: Counter[IssueSeverity]
    category_counts: Counter[IssueCategory]

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def total_occurrences(self) -> int:
        return len(self.occurrences)

    @property
    def finding_representatives(self) -> list[ReviewIssue]:
        return [finding.representative for finding in self.findings]

    @property
    def occurrence_representatives(self) -> list[ReviewIssue]:
        return [occurrence.representative for occurrence in self.occurrences]


def build_report_aggregate(issues: list[ReviewIssue]) -> ReportAggregate:
    """Build one canonical snapshot from persisted detector rows."""

    review_target_issues = [
        issue for issue in issues if is_review_target_path(issue.file_path)
    ]
    grouped_issues: dict[str, list[ReviewIssue]] = {}
    for issue in review_target_issues:
        grouped_issues.setdefault(review_issue_finding_key(issue), []).append(issue)

    findings = tuple(
        _canonical_finding(finding_key, finding_issues)
        for finding_key, finding_issues in sorted(grouped_issues.items())
    )
    occurrences = tuple(
        occurrence for finding in findings for occurrence in finding.occurrences
    )
    finding_representatives = [finding.representative for finding in findings]
    return ReportAggregate(
        findings=findings,
        occurrences=occurrences,
        raw_issue_count=len(review_target_issues),
        severity_counts=Counter(issue.severity for issue in finding_representatives),
        category_counts=Counter(issue.category for issue in finding_representatives),
    )


def canonical_occurrences(issues: list[ReviewIssue]) -> tuple[CanonicalOccurrence, ...]:
    """Merge rows with the same claim and overlapping normalized source ranges."""

    occurrence_rows: list[list[ReviewIssue]] = []
    for issue in sorted(issues, key=_occurrence_sort_key):
        normalized_path = normalize_finding_path(issue.file_path)
        overlapping = next(
            (
                rows
                for rows in occurrence_rows
                if normalize_finding_path(rows[0].file_path) == normalized_path
                and min(row.line_start for row in rows) <= issue.line_end
                and max(row.line_end for row in rows) >= issue.line_start
            ),
            None,
        )
        if overlapping is None:
            occurrence_rows.append([issue])
        else:
            overlapping.append(issue)

    return tuple(
        CanonicalOccurrence(
            representative=representative_issue(rows),
            issues=tuple(rows),
        )
        for rows in occurrence_rows
    )


def representative_issue(issues: list[ReviewIssue]) -> ReviewIssue:
    """Choose a stable, evidence-rich representative from equivalent rows."""

    return min(issues, key=_representative_sort_key)


def build_verified_summary(
    aggregate: ReportAggregate,
    *,
    total_files_analyzed: int,
) -> str:
    """Build the authoritative numeric report summary."""

    severity = aggregate.severity_counts
    category = aggregate.category_counts
    return (
        f"Review analyzed {total_files_analyzed} files and confirmed "
        f"{aggregate.total_findings} findings across "
        f"{aggregate.total_occurrences} source occurrences. Severity: "
        f"{severity[IssueSeverity.CRITICAL]} critical, "
        f"{severity[IssueSeverity.HIGH]} high, "
        f"{severity[IssueSeverity.MEDIUM]} medium, "
        f"{severity[IssueSeverity.LOW]} low, and "
        f"{severity[IssueSeverity.INFO]} informational. Categories: "
        f"security={category[IssueCategory.SECURITY]}, "
        f"bug={category[IssueCategory.BUG]}, "
        f"performance={category[IssueCategory.PERFORMANCE]}, "
        f"maintainability={category[IssueCategory.MAINTAINABILITY]}, "
        f"style={category[IssueCategory.STYLE]}, and "
        f"requirement={category[IssueCategory.REQUIREMENT]}."
    )


def _canonical_finding(
    finding_key: str,
    issues: list[ReviewIssue],
) -> CanonicalFinding:
    occurrences = canonical_occurrences(issues)
    return CanonicalFinding(
        finding_key=finding_key,
        representative=representative_issue(
            [occurrence.representative for occurrence in occurrences]
        ),
        occurrences=occurrences,
        raw_issues=tuple(issues),
    )


def _occurrence_sort_key(issue: ReviewIssue) -> tuple[str, int, int, str]:
    return (
        normalize_finding_path(issue.file_path),
        issue.line_start,
        issue.line_end,
        str(issue.id),
    )


def _representative_sort_key(issue: ReviewIssue) -> tuple[object, ...]:
    confidence = issue.confidence if issue.confidence is not None else -1.0
    return (
        SEVERITY_SORT_ORDER[issue.severity],
        not _has_rich_evidence(issue),
        SOURCE_SORT_ORDER[issue.source],
        -confidence,
        str(issue.id),
    )


def _has_rich_evidence(issue: ReviewIssue) -> bool:
    raw_output = issue.raw_output
    if not isinstance(raw_output, dict):
        return False
    if isinstance(raw_output.get("source_context"), dict):
        return True
    probe_review = raw_output.get("probe_review")
    if not isinstance(probe_review, dict):
        return False
    return bool(probe_review.get("supporting_evidence")) or isinstance(
        probe_review.get("source_context"), dict
    )
