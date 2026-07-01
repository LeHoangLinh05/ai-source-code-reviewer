"""Review report and issue query API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_current_user, get_report_service
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.models.user import User
from app.schemas.report import (
    IssueListResponse,
    IssueResponse,
    ReportResponse,
    ReportSummaryResponse,
)
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/{job_id}",
    response_model=ReportResponse,
    summary="Get full review report",
)
async def get_report(
    job_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
) -> ReportResponse:
    """Return the full report for an authorized completed review job."""

    report = await report_service.get_report(job_id, current_user)
    return ReportResponse.model_validate(report)


@router.get(
    "/{job_id}/summary",
    response_model=ReportSummaryResponse,
    summary="Get review report summary",
)
async def get_report_summary(
    job_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
) -> ReportSummaryResponse:
    """Return executive summary and scores for an authorized report."""

    return await report_service.get_summary(job_id, current_user)


@router.get(
    "/{job_id}/issues",
    response_model=IssueListResponse,
    summary="List review issues",
)
async def list_report_issues(
    job_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
    severity: Annotated[IssueSeverity | None, Query()] = None,
    category: Annotated[IssueCategory | None, Query()] = None,
    source: Annotated[IssueSource | None, Query()] = None,
    file_path: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str, Query(min_length=1, max_length=32)] = "-created_at",
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
    current_user: Annotated[User, Depends(get_current_user)],
    report_service: Annotated[ReportService, Depends(get_report_service)],
) -> IssueResponse:
    """Return one issue for an authorized report."""

    return await report_service.get_issue(
        job_id=job_id,
        issue_id=issue_id,
        current_user=current_user,
    )
