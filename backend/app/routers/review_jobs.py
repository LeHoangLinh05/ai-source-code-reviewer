"""Review job lifecycle API routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import (
    get_current_user,
    get_review_job_service,
    rate_limit_review_job_create,
)
from app.models.review_job import ReviewJobStatus
from app.models.user import User
from app.schemas.review_job import (
    ReviewJobCancelResponse,
    ReviewJobCreate,
    ReviewJobCreateResponse,
    ReviewJobResponse,
    ReviewJobStatusUpdate,
)
from app.services.job_service import ReviewJobService

router = APIRouter(prefix="/review-jobs", tags=["review-jobs"])


@router.post(
    "",
    response_model=ReviewJobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a review job",
)
async def create_review_job(
    payload: ReviewJobCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    review_job_service: Annotated[ReviewJobService, Depends(get_review_job_service)],
    _rate_limit: Annotated[None, Depends(rate_limit_review_job_create)],
) -> ReviewJobCreateResponse:
    """Create a pending review job for an owned repository."""

    return await review_job_service.create_job(payload, current_user)


@router.get(
    "",
    response_model=list[ReviewJobResponse],
    summary="List visible review jobs",
)
async def list_review_jobs(
    current_user: Annotated[User, Depends(get_current_user)],
    review_job_service: Annotated[ReviewJobService, Depends(get_review_job_service)],
    job_status: Annotated[ReviewJobStatus | None, Query(alias="status")] = None,
    repository_id: Annotated[UUID | None, Query()] = None,
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
    current_user: Annotated[User, Depends(get_current_user)],
    review_job_service: Annotated[ReviewJobService, Depends(get_review_job_service)],
) -> ReviewJobResponse:
    """Return one review job after owner/admin authorization."""

    return await review_job_service.get_job(job_id, current_user)


@router.delete(
    "/{job_id}",
    response_model=ReviewJobCancelResponse,
    summary="Cancel a review job",
)
async def cancel_review_job(
    job_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    review_job_service: Annotated[ReviewJobService, Depends(get_review_job_service)],
) -> ReviewJobCancelResponse:
    """Cancel a non-completed review job."""

    await review_job_service.cancel_job(job_id, current_user)
    return ReviewJobCancelResponse(message="Review job canceled")


@router.patch(
    "/{job_id}/status",
    response_model=ReviewJobResponse,
    summary="Simulate review job status",
)
async def update_review_job_status(
    job_id: UUID,
    payload: ReviewJobStatusUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    review_job_service: Annotated[ReviewJobService, Depends(get_review_job_service)],
) -> ReviewJobResponse:
    """Dev-only helper for polling UI until the worker pipeline exists."""

    return await review_job_service.update_job_status(job_id, payload, current_user)
