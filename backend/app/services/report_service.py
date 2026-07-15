"""Report aggregation, scoring, and issue query workflows."""

from uuid import UUID

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.models.user import User, UserRole
from app.repositories.mongodb_repository import ChunkMetadataRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.normalized_issue import NormalizedIssue
from app.schemas.report import (
    IssueFilters,
    IssueListResponse,
    IssueResponse,
    ReportScores,
    ReportSummaryResponse,
)
from app.services.report_generation_service import (
    build_top_risky_files,
    calculate_report_scores,
)

ALLOWED_ISSUE_SORT_FIELDS = {
    "created_at",
    "severity",
    "file_path",
}


class ReportService:
    """Business workflows for report and issue queries."""

    def __init__(
        self,
        report_repository: ReportRepository,
        chunk_metadata_repository: ChunkMetadataRepository,
    ) -> None:
        self.report_repository = report_repository
        self.chunk_metadata_repository = chunk_metadata_repository

    async def get_report(self, job_id: UUID, current_user: User) -> ReviewReport:
        """Return the full report for an authorized review job."""

        review_job = await self._ensure_job_access(job_id, current_user)
        report = await self._get_existing_report(job_id, review_job.status)
        await self._refresh_report_aggregates(report)
        return report

    async def get_summary(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReportSummaryResponse:
        """Return executive summary and scores for an authorized report."""

        review_job = await self._ensure_job_access(job_id, current_user)
        report = await self._get_existing_report(job_id, review_job.status)
        await self._refresh_report_aggregates(report)
        return ReportSummaryResponse(
            job_id=report.job_id,
            executive_summary=report.executive_summary,
            scores=ReportScores(
                security_score=report.security_score,
                maintainability_score=report.maintainability_score,
                performance_score=report.performance_score,
                overall_score=report.overall_score,
            ),
        )

    async def list_issues(
        self,
        *,
        job_id: UUID,
        current_user: User,
        severity: IssueSeverity | None,
        category: IssueCategory | None,
        source: IssueSource | None,
        file_path: str | None,
        page: int,
        per_page: int,
        sort: str,
    ) -> IssueListResponse:
        """Return paginated issues for an authorized report."""

        review_job = await self._ensure_job_access(job_id, current_user)
        await self._get_existing_report(job_id, review_job.status)
        sort_field, is_descending = self._parse_sort(sort)
        total = await self.report_repository.count_issues(
            job_id=job_id,
            severity=severity,
            category=category,
            source=source,
            file_path=file_path,
        )
        issues = await self.report_repository.list_issues(
            job_id=job_id,
            severity=severity,
            category=category,
            source=source,
            file_path=file_path,
            page=page,
            per_page=per_page,
            sort_field=sort_field,
            is_descending=is_descending,
        )
        return IssueListResponse(
            total=total,
            page=page,
            per_page=per_page,
            sort=sort,
            filters=IssueFilters(
                severity=severity,
                category=category,
                source=source,
                file_path=file_path,
            ),
            issues=[IssueResponse.model_validate(issue) for issue in issues],
        )

    async def get_issue(
        self,
        *,
        job_id: UUID,
        issue_id: UUID,
        current_user: User,
    ) -> IssueResponse:
        """Return one issue for an authorized report."""

        review_job = await self._ensure_job_access(job_id, current_user)
        await self._get_existing_report(job_id, review_job.status)
        issue = await self.report_repository.get_issue_by_id(
            job_id=job_id,
            issue_id=issue_id,
        )
        if issue is None:
            raise NotFoundError("Review issue not found")

        response = IssueResponse.model_validate(issue)
        if _has_source_context(response.raw_output):
            return response

        chunk = await self.chunk_metadata_repository.find_containing_line(
            job_id=job_id,
            file_path=issue.file_path,
            line_start=issue.line_start,
            line_end=issue.line_end,
        )
        source_context = _source_context_from_chunk(
            chunk,
            line_start=issue.line_start,
            line_end=issue.line_end,
        )
        if source_context is not None:
            response.raw_output = {
                **(response.raw_output or {}),
                "source_context": source_context,
            }
        return response

    async def _ensure_job_access(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReviewJob:
        review_job = await self.report_repository.get_job_by_id(job_id)
        if review_job is None:
            raise NotFoundError("Review job not found")

        if current_user.role == UserRole.ADMIN:
            return review_job

        if review_job.user_id != current_user.id:
            raise AuthorizationError("Review job access is restricted to its owner")

        return review_job

    async def _get_existing_report(
        self,
        job_id: UUID,
        job_status: ReviewJobStatus,
    ) -> ReviewReport:
        report = await self.report_repository.get_report_by_job_id(job_id)
        if report is None:
            raise NotFoundError(f"Report not ready, job status: {job_status.value}")

        return report

    async def _refresh_report_aggregates(self, report: ReviewReport) -> None:
        """Refresh score/count fields from current persisted issues for display."""

        issues = await self.report_repository.list_all_issues(report.job_id)
        normalized_issues = [_to_normalized_issue(issue) for issue in issues]
        scores = calculate_report_scores(normalized_issues)
        severity_counts = {
            severity: sum(1 for issue in issues if issue.severity == severity)
            for severity in IssueSeverity
        }
        report.total_issues = len(issues)
        report.critical_count = severity_counts[IssueSeverity.CRITICAL]
        report.high_count = severity_counts[IssueSeverity.HIGH]
        report.medium_count = severity_counts[IssueSeverity.MEDIUM]
        report.low_count = severity_counts[IssueSeverity.LOW]
        report.info_count = severity_counts[IssueSeverity.INFO]
        report.security_score = scores["security_score"]
        report.maintainability_score = scores["maintainability_score"]
        report.performance_score = scores["performance_score"]
        report.overall_score = scores["overall_score"]
        report.top_risky_files = build_top_risky_files(normalized_issues)

    def _parse_sort(self, sort: str) -> tuple[str, bool]:
        is_descending = sort.startswith("-")
        sort_field = sort[1:] if is_descending else sort
        if sort_field not in ALLOWED_ISSUE_SORT_FIELDS:
            raise BadRequestError(
                "Sort must be one of created_at, severity, file_path, "
                "or prefixed with -"
            )

        return sort_field, is_descending


def _has_source_context(raw_output: dict[str, object] | None) -> bool:
    return isinstance(raw_output, dict) and isinstance(
        raw_output.get("source_context"), dict
    )


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
