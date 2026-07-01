"""Report aggregation, scoring, and issue query workflows."""

from uuid import UUID

from app.core.exceptions import AuthorizationError, BadRequestError, NotFoundError
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.review_report import ReviewReport
from app.models.user import User, UserRole
from app.repositories.report_repository import ReportRepository
from app.schemas.report import (
    IssueFilters,
    IssueListResponse,
    IssueResponse,
    ReportScores,
    ReportSummaryResponse,
)

ALLOWED_ISSUE_SORT_FIELDS = {
    "created_at",
    "severity",
    "file_path",
}


class ReportService:
    """Business workflows for report and issue queries."""

    def __init__(self, report_repository: ReportRepository) -> None:
        self.report_repository = report_repository

    async def get_report(self, job_id: UUID, current_user: User) -> ReviewReport:
        """Return the full report for an authorized review job."""

        review_job = await self._ensure_job_access(job_id, current_user)
        return await self._get_existing_report(job_id, review_job.status)

    async def get_summary(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReportSummaryResponse:
        """Return executive summary and scores for an authorized report."""

        review_job = await self._ensure_job_access(job_id, current_user)
        report = await self._get_existing_report(job_id, review_job.status)
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

        return IssueResponse.model_validate(issue)

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

    def _parse_sort(self, sort: str) -> tuple[str, bool]:
        is_descending = sort.startswith("-")
        sort_field = sort[1:] if is_descending else sort
        if sort_field not in ALLOWED_ISSUE_SORT_FIELDS:
            raise BadRequestError(
                "Sort must be one of created_at, severity, file_path, "
                "or prefixed with -"
            )

        return sort_field, is_descending
