"""Persistence operations for review reports and issues."""

from uuid import UUID

from sqlalchemy import Select, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.normalized_issue import NormalizedIssue
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.models.review_job import ReviewJob
from app.models.review_report import ReviewReport


class ReportRepository:
    """Database access for report and issue records without business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_job_by_id(self, job_id: UUID) -> ReviewJob | None:
        """Return the review job that owns report data."""

        return await self.session.get(ReviewJob, job_id)

    async def get_report_by_job_id(self, job_id: UUID) -> ReviewReport | None:
        """Return the report for a completed review job."""

        statement = select(ReviewReport).where(ReviewReport.job_id == job_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def count_issues(
        self,
        *,
        job_id: UUID,
        severity: IssueSeverity | None,
        category: IssueCategory | None,
        source: IssueSource | None,
        file_path: str | None,
    ) -> int:
        """Count issues matching report filters."""

        statement = select(func.count()).select_from(
            self._issue_filter_statement(
                job_id=job_id,
                severity=severity,
                category=category,
                source=source,
                file_path=file_path,
            ).subquery()
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def list_issues(
        self,
        *,
        job_id: UUID,
        severity: IssueSeverity | None,
        category: IssueCategory | None,
        source: IssueSource | None,
        file_path: str | None,
        page: int,
        per_page: int,
        sort_field: str,
        is_descending: bool,
    ) -> list[ReviewIssue]:
        """Return paginated issues matching report filters."""

        sort_column = getattr(ReviewIssue, sort_field)
        order_by = sort_column.desc() if is_descending else sort_column.asc()
        # Roadmap compliance findings are a data-contract priority override:
        # later Agent/static/dedup merge layers must never lower severity or delete
        # source=roadmap_rule issues, and report sorting keeps them before all
        # AI/static findings even when the user sorts by report priority/severity.
        roadmap_priority = case(
            (ReviewIssue.source == IssueSource.ROADMAP_RULE, 0), else_=1
        )
        statement = (
            self._issue_filter_statement(
                job_id=job_id,
                severity=severity,
                category=category,
                source=source,
                file_path=file_path,
            )
            .order_by(roadmap_priority.asc(), order_by, ReviewIssue.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_issue_by_id(
        self,
        *,
        job_id: UUID,
        issue_id: UUID,
    ) -> ReviewIssue | None:
        """Return a single issue that belongs to a job."""

        statement = select(ReviewIssue).where(
            ReviewIssue.job_id == job_id,
            ReviewIssue.id == issue_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def replace_analysis_results(
        self,
        *,
        report: ReviewReport,
        issues: list[NormalizedIssue],
    ) -> ReviewReport:
        """Replace generated report and issues for one review job."""

        await self.session.execute(
            delete(ReviewIssue).where(ReviewIssue.job_id == report.job_id)
        )
        existing_report = await self.get_report_by_job_id(report.job_id)
        if existing_report is not None:
            await self.session.delete(existing_report)
            await self.session.flush()

        self.session.add(report)
        self.session.add_all(
            [
                ReviewIssue(
                    job_id=report.job_id,
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
                )
                for issue in issues
            ]
        )
        await self.session.commit()
        await self.session.refresh(report)
        return report

    def _issue_filter_statement(
        self,
        *,
        job_id: UUID,
        severity: IssueSeverity | None,
        category: IssueCategory | None,
        source: IssueSource | None,
        file_path: str | None,
    ) -> Select[tuple[ReviewIssue]]:
        statement = select(ReviewIssue).where(ReviewIssue.job_id == job_id)
        if severity is not None:
            statement = statement.where(ReviewIssue.severity == severity)
        if category is not None:
            statement = statement.where(ReviewIssue.category == category)
        if source is not None:
            statement = statement.where(ReviewIssue.source == source)
        if file_path is not None:
            statement = statement.where(ReviewIssue.file_path.ilike(f"%{file_path}%"))
        return statement
