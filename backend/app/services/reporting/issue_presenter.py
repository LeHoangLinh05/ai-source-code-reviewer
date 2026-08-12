"""Group review issues and build report API representations."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.review_targets import is_review_target_path
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.schemas.normalized_issue import NormalizedIssue
from app.schemas.report import IssueOccurrenceResponse, IssueResponse
from app.services.reporting.aggregation import (
    SEVERITY_SORT_ORDER,
    CanonicalOccurrence,
    build_report_aggregate,
    canonical_occurrences,
    representative_issue,
)
from app.services.reporting.finding_identity import (
    normalize_finding_path,
    review_issue_finding_key,
)

AI_REVIEW_SOURCES = frozenset({IssueSource.AI_REVIEW, IssueSource.KB})


def _has_source_context(raw_output: dict[str, object] | None) -> bool:
    return isinstance(raw_output, dict) and isinstance(
        raw_output.get("source_context"), dict
    )


def _group_issues(issues: list[ReviewIssue]) -> dict[str, list[ReviewIssue]]:
    groups: dict[str, list[ReviewIssue]] = {}
    for issue in issues:
        if not is_review_target_path(issue.file_path):
            continue
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
    return representative_issue(issues)


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
    review_issues = [
        _review_issue_from_response(issue)
        if isinstance(issue, IssueResponse)
        else issue
        for issue in issues
    ]
    occurrences = _canonical_occurrences(review_issues)
    response.group_key = group_key
    response.occurrence_count = len(occurrences)
    response.raw_issue_count = len(review_issues)
    response.affected_files = sorted(
        {
            normalize_finding_path(occurrence.representative.file_path)
            for occurrence in occurrences
        }
    )
    response.primary_issue_id = representative.id
    response.fix_issue_ids = [
        occurrence.representative.id for occurrence in occurrences
    ]
    if include_occurrences:
        response.occurrences = [
            IssueOccurrenceResponse(
                issue_id=occurrence.representative.id,
                raw_issue_ids=[issue.id for issue in occurrence.issues],
                sources=sorted(
                    {issue.source for issue in occurrence.issues},
                    key=lambda source: source.value,
                ),
                file_path=occurrence.representative.file_path,
                line_start=min(issue.line_start for issue in occurrence.issues),
                line_end=max(issue.line_end for issue in occurrence.issues),
                title=occurrence.representative.title,
                description=occurrence.representative.description,
                suggestion=occurrence.representative.suggestion,
                confidence=occurrence.representative.confidence,
                raw_output=occurrence.representative.raw_output,
                created_at=occurrence.representative.created_at,
            )
            for occurrence in occurrences
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
    return review_issue_finding_key(issue)


def _canonical_occurrences(
    issues: list[ReviewIssue],
) -> list[CanonicalOccurrence]:
    return list(canonical_occurrences(issues))


def _canonical_representative_issues(
    issues: list[ReviewIssue],
) -> list[ReviewIssue]:
    """Return one representative raw row for every canonical occurrence."""

    return build_report_aggregate(issues).occurrence_representatives


def _canonical_finding_representative_issues(
    issues: list[ReviewIssue],
) -> list[ReviewIssue]:
    """Return one representative row for every canonical finding."""

    return build_report_aggregate(issues).finding_representatives


def _group_matches_filters(
    issues: list[ReviewIssue],
    *,
    severity: IssueSeverity | None,
    category: IssueCategory | None,
    source: IssueSource | None,
    file_path: str | None,
) -> bool:
    """Evaluate filters against a complete canonical finding group."""

    representative = _representative_issue(issues)
    if severity is not None and representative.severity is not severity:
        return False
    if category is not None and representative.category is not category:
        return False
    if source is IssueSource.AI_REVIEW:
        if all(issue.source not in AI_REVIEW_SOURCES for issue in issues):
            return False
    elif source is not None and all(issue.source is not source for issue in issues):
        return False
    if file_path is not None:
        normalized_filter = file_path.replace("\\", "/").casefold()
        if all(
            normalized_filter not in issue.file_path.replace("\\", "/").casefold()
            for issue in issues
        ):
            return False
    return True


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
