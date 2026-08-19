"""Report aggregation, scoring, and issue query workflows."""

from uuid import UUID

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.core.review_targets import is_review_target_path
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.models.user import User
from app.repositories.mongodb_repository import ChunkMetadataRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.report import (
    IssueFilters,
    IssueListResponse,
    IssueResponse,
    ReportResponse,
    ReportScores,
    ReportSummaryResponse,
)
from app.services.reporting.aggregation import (
    ReportAggregate,
    build_report_aggregate,
    build_verified_summary,
)
from app.services.reporting.generation import build_top_risky_files
from app.services.reporting.issue_presenter import (
    _group_issues,
    _group_matches_filters,
    _has_source_context,
    _issue_group_key,
    _issue_group_response,
    _sort_issue_groups,
    _source_context_from_chunk,
    _to_normalized_issue,
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

    async def get_report(self, job_id: UUID, current_user: User) -> ReportResponse:
        """Return the full report for an authorized review job."""

        review_job = await self._ensure_job_access(job_id, current_user)
        report = await self._get_existing_report(job_id, review_job.status)
        aggregate = await self._refresh_report_aggregates(report)
        response = ReportResponse.model_validate(report)
        response.total_findings = aggregate.total_findings
        response.total_occurrences = aggregate.total_occurrences
        response.total_raw_issues = aggregate.raw_issue_count
        return response

    async def get_summary(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReportSummaryResponse:
        """Return the executive summary and deprecated nullable score fields."""

        review_job = await self._ensure_job_access(job_id, current_user)
        report = await self._get_existing_report(job_id, review_job.status)
        aggregate = await self._refresh_report_aggregates(report)
        return ReportSummaryResponse(
            job_id=report.job_id,
            executive_summary=report.executive_summary,
            analysis_overview=report.analysis_overview,
            scores=ReportScores(
                security_score=report.security_score,
                maintainability_score=report.maintainability_score,
                performance_score=report.performance_score,
                overall_score=report.overall_score,
            ),
            total_findings=aggregate.total_findings,
            total_occurrences=aggregate.total_occurrences,
            total_raw_issues=aggregate.raw_issue_count,
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
        issues = await self.report_repository.list_all_issues(job_id)
        groups = {
            group_key: grouped_issues
            for group_key, grouped_issues in _group_issues(issues).items()
            if _group_matches_filters(
                grouped_issues,
                severity=severity,
                category=category,
                source=source,
                file_path=file_path,
            )
        }
        sorted_groups = _sort_issue_groups(
            groups,
            sort_field=sort_field,
            is_descending=is_descending,
        )
        paginated_groups = sorted_groups[(page - 1) * per_page : page * per_page]
        return IssueListResponse(
            total=len(groups),
            page=page,
            per_page=per_page,
            sort=sort,
            filters=IssueFilters(
                severity=severity,
                category=category,
                source=source,
                file_path=file_path,
            ),
            issues=[
                _issue_group_response(group_key, grouped_issues)
                for group_key, grouped_issues in paginated_groups
            ],
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
        if issue is None or not is_review_target_path(issue.file_path):
            raise NotFoundError("Review issue not found")

        group_key = _issue_group_key(issue)
        all_issues = await self.report_repository.list_all_issues(job_id)
        grouped_issues = [
            grouped_issue
            for grouped_issue in all_issues
            if _issue_group_key(grouped_issue) == group_key
        ]
        enriched_issues = [
            await self._issue_with_source_context(grouped_issue)
            for grouped_issue in grouped_issues
        ]
        return _issue_group_response(
            group_key, enriched_issues, include_occurrences=True
        )

    async def _issue_with_source_context(self, issue: ReviewIssue) -> IssueResponse:
        response = IssueResponse.model_validate(issue)
        raw_output = response.raw_output or {}

        # Already has top-level source context
        if isinstance(raw_output.get("source_context"), dict):
            return response

        # Promote source context from probe_review to top-level
        probe_review = raw_output.get("probe_review")
        if isinstance(probe_review, dict) and isinstance(
            probe_review.get("source_context"), dict
        ):
            response.raw_output = {
                **raw_output,
                "source_context": probe_review["source_context"],
            }
            return response

        # Fallback: look up chunk metadata from MongoDB
        chunk = await self.chunk_metadata_repository.find_containing_line(
            job_id=issue.job_id,
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
                **raw_output,
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

    async def _refresh_report_aggregates(
        self,
        report: ReviewReport,
    ) -> ReportAggregate:
        """Refresh persisted report fields from one canonical snapshot."""

        issues = await self.report_repository.list_all_issues(report.job_id)
        aggregate = build_report_aggregate(issues)
        normalized_occurrences = [
            _to_normalized_issue(issue)
            for issue in aggregate.occurrence_representatives
        ]
        refreshed_values = {
            "total_issues": aggregate.total_findings,
            "critical_count": aggregate.severity_counts[IssueSeverity.CRITICAL],
            "high_count": aggregate.severity_counts[IssueSeverity.HIGH],
            "medium_count": aggregate.severity_counts[IssueSeverity.MEDIUM],
            "low_count": aggregate.severity_counts[IssueSeverity.LOW],
            "info_count": aggregate.severity_counts[IssueSeverity.INFO],
            "security_score": None,
            "maintainability_score": None,
            "performance_score": None,
            "overall_score": None,
            "top_risky_files": build_top_risky_files(normalized_occurrences),
            "executive_summary": build_verified_summary(
                aggregate,
                total_files_analyzed=report.total_files_analyzed,
            ),
        }
        has_changes = any(
            getattr(report, field_name) != field_value
            for field_name, field_value in refreshed_values.items()
        )
        for field_name, field_value in refreshed_values.items():
            setattr(report, field_name, field_value)
        if has_changes:
            await self.report_repository.save_report(report)
        return aggregate

    def _parse_sort(self, sort: str) -> tuple[str, bool]:
        is_descending = sort.startswith("-")
        sort_field = sort[1:] if is_descending else sort
        if sort_field not in ALLOWED_ISSUE_SORT_FIELDS:
            raise BadRequestError(
                "Sort must be one of created_at, severity, file_path, "
                "or prefixed with -"
            )

        return sort_field, is_descending
