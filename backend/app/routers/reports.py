"""Review report and issue query API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.dependencies import CurrentUserDep, ReportServiceDep
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.report import (
    IssueListResponse,
    IssueResponse,
    ReportResponse,
    ReportSummaryResponse,
)

router = APIRouter(prefix="/reports", tags=["reports"])
MIN_ISSUE_PAGE = 1
DEFAULT_ISSUE_PAGE = 1
DEFAULT_ISSUE_PER_PAGE = 20
MAX_ISSUE_PER_PAGE = 100
MAX_ISSUE_FILE_PATH_LENGTH = 255
MAX_ISSUE_SORT_LENGTH = 32
DEFAULT_ISSUE_SORT = "-created_at"
IssueSeverityFilter = Annotated[IssueSeverity | None, Query()]
IssueCategoryFilter = Annotated[IssueCategory | None, Query()]
IssueSourceFilter = Annotated[IssueSource | None, Query()]
IssueFilePathFilter = Annotated[
    str | None,
    Query(min_length=1, max_length=MAX_ISSUE_FILE_PATH_LENGTH),
]
IssuePageQuery = Annotated[int, Query(ge=MIN_ISSUE_PAGE)]
IssuePerPageQuery = Annotated[int, Query(ge=MIN_ISSUE_PAGE, le=MAX_ISSUE_PER_PAGE)]
IssueSortQuery = Annotated[
    str,
    Query(min_length=1, max_length=MAX_ISSUE_SORT_LENGTH),
]


@router.get(
    "/{job_id}",
    response_model=ReportResponse,
    summary="Get full review report",
)
async def get_report(
    job_id: UUID,
    current_user: CurrentUserDep,
    report_service: ReportServiceDep,
) -> ReportResponse:
    """Return the full report for an authorized completed review job."""

    return await report_service.get_report(job_id, current_user)


@router.get(
    "/{job_id}/summary",
    response_model=ReportSummaryResponse,
    summary="Get review report summary",
)
async def get_report_summary(
    job_id: UUID,
    current_user: CurrentUserDep,
    report_service: ReportServiceDep,
) -> ReportSummaryResponse:
    """Return the executive summary for an authorized report."""

    return await report_service.get_summary(job_id, current_user)


@router.get(
    "/{job_id}/issues",
    response_model=IssueListResponse,
    summary="List review issues",
)
async def list_report_issues(
    job_id: UUID,
    current_user: CurrentUserDep,
    report_service: ReportServiceDep,
    severity: IssueSeverityFilter = None,
    category: IssueCategoryFilter = None,
    source: IssueSourceFilter = None,
    file_path: IssueFilePathFilter = None,
    page: IssuePageQuery = DEFAULT_ISSUE_PAGE,
    per_page: IssuePerPageQuery = DEFAULT_ISSUE_PER_PAGE,
    sort: IssueSortQuery = DEFAULT_ISSUE_SORT,
) -> IssueListResponse:
    """Return filtered and paginated issues for an authorized report."""

    return await report_service.list_issues(
        job_id=job_id,
        current_user=current_user,
        severity=severity,
        category=category,
        source=source,
        file_path=file_path,
        page=page,
        per_page=per_page,
        sort=sort,
    )


@router.get(
    "/{job_id}/issues/{issue_id}",
    response_model=IssueResponse,
    summary="Get review issue details",
)
async def get_report_issue(
    job_id: UUID,
    issue_id: UUID,
    current_user: CurrentUserDep,
    report_service: ReportServiceDep,
) -> IssueResponse:
    """Return one issue for an authorized report."""

    return await report_service.get_issue(
        job_id=job_id,
        issue_id=issue_id,
        current_user=current_user,
    )
