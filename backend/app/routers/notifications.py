"""Realtime notification and SSE API routes."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUserDep, RedisDep, ReviewJobServiceDep
from app.services.review_jobs.notifications import stream_job_progress

router = APIRouter(prefix="/review-jobs", tags=["notifications"])


@router.get(
    "/{job_id}/stream",
    response_class=StreamingResponse,
    summary="Stream review job progress",
)
async def stream_review_job_progress(
    job_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    review_job_service: ReviewJobServiceDep,
    redis_client: RedisDep,
) -> StreamingResponse:
    """Stream owner-scoped job progress from an isolated Redis channel."""

    fallback_event = await review_job_service.get_progress_snapshot(
        job_id,
        current_user,
    )
    return StreamingResponse(
        stream_job_progress(
            job_id=job_id,
            fallback_event=fallback_event,
            request=request,
            redis_client=redis_client,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
