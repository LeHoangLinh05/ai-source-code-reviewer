"""Group review issues and build report API representations."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.models.review_issue import IssueSeverity, ReviewIssue
from app.schemas.normalized_issue import NormalizedIssue
from app.schemas.report import IssueOccurrenceResponse, IssueResponse

ISSUE_RULE_ID_FIELDS = ("code", "test_id", "ruleId")
SEVERITY_SORT_ORDER = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.HIGH: 1,
    IssueSeverity.MEDIUM: 2,
    IssueSeverity.LOW: 3,
    IssueSeverity.INFO: 4,
}


def _has_source_context(raw_output: dict[str, object] | None) -> bool:
    return isinstance(raw_output, dict) and isinstance(
        raw_output.get("source_context"), dict
    )


def _group_issues(issues: list[ReviewIssue]) -> dict[str, list[ReviewIssue]]:
    groups: dict[str, list[ReviewIssue]] = {}
    for issue in issues:
        groups.setdefault(_issue_group_key(issue), []).append(issue)
    return groups


def _sort_issue_groups(
    groups: dict[str, list[ReviewIssue]],
    *,
    sort_field: str,
    is_descending: bool,
) -> list[tuple[str, list[ReviewIssue]]]:
    return sorted(
        groups.items(),
        key=lambda item: (
            _issue_group_sort_key(item[1], sort_field),
            _representative_issue(item[1]).created_at,
        ),
        reverse=is_descending,
    )


def _issue_group_sort_key(issues: list[ReviewIssue], sort_field: str) -> object:
    representative = _representative_issue(issues)
    if sort_field == "severity":
        return SEVERITY_SORT_ORDER[representative.severity]
    return getattr(representative, sort_field)


def _representative_issue(issues: list[ReviewIssue]) -> ReviewIssue:
    return sorted(
        issues,
        key=lambda issue: (
            SEVERITY_SORT_ORDER[issue.severity],
            issue.created_at,
            issue.file_path,
            issue.line_start,
        ),
    )[0]


def _issue_group_response(
    group_key: str,
    issues: Sequence[ReviewIssue | IssueResponse],
    *,
    include_occurrences: bool = False,
) -> IssueResponse:
    representative = _representative_issue(
        [
            _review_issue_from_response(issue)
            if isinstance(issue, IssueResponse)
            else issue
            for issue in issues
        ]
    )
    response = IssueResponse.model_validate(representative)
    sorted_issues = sorted(
        issues,
        key=lambda issue: (
            issue.file_path,
            issue.line_start,
            issue.line_end,
            issue.created_at,
        ),
    )
    response.group_key = group_key
    response.occurrence_count = len(issues)
    response.affected_files = sorted({issue.file_path for issue in issues})
    response.primary_issue_id = representative.id
    if include_occurrences:
        response.occurrences = [
            IssueOccurrenceResponse(
                issue_id=issue.id,
                file_path=issue.file_path,
                line_start=issue.line_start,
                line_end=issue.line_end,
                title=issue.title,
                description=issue.description,
                suggestion=issue.suggestion,
                confidence=issue.confidence,
                raw_output=issue.raw_output,
                created_at=issue.created_at,
            )
            for issue in sorted_issues
        ]
    return response


def _review_issue_from_response(issue: IssueResponse) -> ReviewIssue:
    return ReviewIssue(
        id=issue.id,
        job_id=issue.job_id,
        file_path=issue.file_path,
        line_start=issue.line_start,
        line_end=issue.line_end,
        severity=issue.severity,
        category=issue.category,
        title=issue.title,
        description=issue.description,
        suggestion=issue.suggestion,
        source=issue.source,
        confidence=issue.confidence,
        raw_output=issue.raw_output,
        created_at=issue.created_at,
    )


def _issue_group_key(issue: ReviewIssue) -> str:
    rule_id = _issue_rule_id(issue.raw_output)
    if rule_id is not None:
        return "|".join(
            [
                issue.source.value,
                issue.category.value,
                issue.severity.value,
                _normalize_group_text(rule_id),
            ]
        )

    return "|".join(
        [
            issue.source.value,
            issue.category.value,
            issue.severity.value,
            _normalize_group_text(issue.title),
        ]
    )


def _issue_rule_id(raw_output: dict[str, object] | None) -> str | None:
    if raw_output is None:
        return None
    for field in ISSUE_RULE_ID_FIELDS:
        value = raw_output.get(field)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _normalize_group_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _to_normalized_issue(review_issue: ReviewIssue) -> NormalizedIssue:
    return NormalizedIssue(
        file_path=review_issue.file_path,
        line_start=review_issue.line_start,
        line_end=review_issue.line_end,
        severity=review_issue.severity,
        category=review_issue.category,
        title=review_issue.title,
        description=review_issue.description,
        suggestion=review_issue.suggestion,
        source=review_issue.source,
        confidence=review_issue.confidence,
        raw_output=review_issue.raw_output,
    )


def _source_context_from_chunk(
    chunk: dict[str, object] | None,
    *,
    line_start: int,
    line_end: int,
    context_radius: int = 3,
) -> dict[str, object] | None:
    if chunk is None:
        return None
    chunk_text = chunk.get("chunk_text")
    chunk_line_start = chunk.get("line_start")
    if not isinstance(chunk_text, str) or not isinstance(chunk_line_start, int):
        return None

    lines = chunk_text.splitlines()
    first_line = max(chunk_line_start, line_start - context_radius)
    chunk_line_end = chunk_line_start + len(lines) - 1
    last_line = min(chunk_line_end, line_end + context_radius)
    first_index = first_line - chunk_line_start
    last_index = last_line - chunk_line_start + 1
    return {
        "start_line": first_line,
        "lines": lines[first_index:last_index],
    }
