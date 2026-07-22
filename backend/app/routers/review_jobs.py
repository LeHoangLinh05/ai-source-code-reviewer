"""Review job lifecycle API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.dependencies import (
    AITraceServiceDep,
    CurrentUserDep,
    ReviewJobCreateRateLimitDep,
    ReviewJobServiceDep,
)
from app.models.review_job import ReviewJobStatus
from app.schemas.ai_trace import AITraceResponse
from app.schemas.review_job import (
    ReviewJobCancelResponse,
    ReviewJobCreate,
    ReviewJobCreateResponse,
    ReviewJobResponse,
    ReviewJobStatusUpdate,
)

router = APIRouter(prefix="/review-jobs", tags=["review-jobs"])
REVIEW_JOB_CANCELED_MESSAGE = "Review job canceled"
ReviewJobStatusFilter = Annotated[ReviewJobStatus | None, Query(alias="status")]
RepositoryIdFilter = Annotated[UUID | None, Query()]


@router.post(
    "",
    response_model=ReviewJobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a review job",
)
async def create_review_job(
    payload: ReviewJobCreate,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
    _rate_limit: ReviewJobCreateRateLimitDep,
) -> ReviewJobCreateResponse:
    """Create a pending review job for an owned repository."""

    return await review_job_service.create_job(payload, current_user)


@router.get(
    "",
    response_model=list[ReviewJobResponse],
    summary="List visible review jobs",
)
async def list_review_jobs(
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
    job_status: ReviewJobStatusFilter = None,
    repository_id: RepositoryIdFilter = None,
) -> list[ReviewJobResponse]:
    """List jobs created by the user, or all jobs for admins."""

    return await review_job_service.list_jobs(
        current_user,
        status=job_status,
        repository_id=repository_id,
    )


@router.get(
    "/{job_id}",
    response_model=ReviewJobResponse,
    summary="Get review job details",
)
async def get_review_job(
    job_id: UUID,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
) -> ReviewJobResponse:
    """Return one review job after owner/admin authorization."""

    return await review_job_service.get_job(job_id, current_user)


@router.get(
    "/{job_id}/ai-trace",
    response_model=AITraceResponse,
    summary="Get AI review execution trace",
)
async def get_review_job_ai_trace(
    job_id: UUID,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
    ai_trace_service: AITraceServiceDep,
) -> AITraceResponse:
    """Return compact AI tool-call trace and issue-source counts for one job."""

    await review_job_service.get_job(job_id, current_user)
    return await ai_trace_service.get_trace(job_id)


@router.delete(
    "/{job_id}",
    response_model=ReviewJobCancelResponse,
    summary="Cancel a review job",
)
async def cancel_review_job(
    job_id: UUID,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
) -> ReviewJobCancelResponse:
    """Cancel a non-completed review job."""

    await review_job_service.cancel_job(job_id, current_user)
    return ReviewJobCancelResponse(message=REVIEW_JOB_CANCELED_MESSAGE)


@router.patch(
    "/{job_id}/status",
    response_model=ReviewJobResponse,
    summary="Simulate review job status",
)
async def update_review_job_status(
    job_id: UUID,
    payload: ReviewJobStatusUpdate,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
) -> ReviewJobResponse:
    """Dev-only helper for polling UI until the worker pipeline exists."""

    return await review_job_service.update_job_status(job_id, payload, current_user)
