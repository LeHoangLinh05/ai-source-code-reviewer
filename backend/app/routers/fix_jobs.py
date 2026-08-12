"""Fix job lifecycle API routes."""

from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from app.core.dependencies import (
    CurrentUserDep,
    FixJobServiceDep,
    FixPublishServiceDep,
    RedisDep,
)
from app.schemas.fix_job import (
    FixAuditLogResponse,
    FixDiffResponse,
    FixJobCreate,
    FixJobResponse,
    PublishFixPayload,
)
from app.services.fix_notification_service import stream_fix_job_progress

router = APIRouter(tags=["fix-jobs"])


@router.post(
    "/review-jobs/{job_id}/fixes",
    response_model=FixJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a fix job",
)
async def create_fix_job(
    job_id: UUID,
    payload: FixJobCreate,
    current_user: CurrentUserDep,
    fix_job_service: FixJobServiceDep,
) -> FixJobResponse:
    """Create a queued patch-generation job for selected review issues."""

    return await fix_job_service.create_fix(job_id, payload, current_user)


@router.get(
    "/review-jobs/{job_id}/fixes",
    response_model=list[FixJobResponse],
    summary="List fix jobs for a review",
)
async def list_fix_jobs(
    job_id: UUID,
    current_user: CurrentUserDep,
    fix_job_service: FixJobServiceDep,
) -> list[FixJobResponse]:
    """Return fix jobs for an authorized review job."""

    return await fix_job_service.list_fixes(job_id, current_user)


@router.get(
    "/fixes/{fix_id}",
    response_model=FixJobResponse,
    summary="Get fix job details",
)
async def get_fix_job(
    fix_id: UUID,
    current_user: CurrentUserDep,
    fix_job_service: FixJobServiceDep,
) -> FixJobResponse:
    """Return one fix job after ownership authorization."""

    return await fix_job_service.get_fix(fix_id, current_user)


@router.get(
    "/fixes/{fix_id}/diff",
    response_model=FixDiffResponse,
    summary="Get fix job diff",
)
async def get_fix_job_diff(
    fix_id: UUID,
    current_user: CurrentUserDep,
    fix_job_service: FixJobServiceDep,
) -> FixDiffResponse:
    """Return the generated unified diff for a fix job."""

    return await fix_job_service.get_diff(fix_id, current_user)


@router.post(
    "/fixes/{fix_id}/publish",
    response_model=FixJobResponse,
    summary="Publish fix as pull request",
)
async def publish_fix_job(
    fix_id: UUID,
    payload: PublishFixPayload,
    current_user: CurrentUserDep,
    fix_publish_service: FixPublishServiceDep,
) -> FixJobResponse:
    """Approve the generated patch and enqueue PR publishing."""

    return await fix_publish_service.publish_fix(fix_id, payload, current_user)


@router.post(
    "/fixes/{fix_id}/retry-publish",
    response_model=FixJobResponse,
    summary="Retry a failed fix publish",
)
async def retry_fix_publish(
    fix_id: UUID,
    current_user: CurrentUserDep,
    fix_publish_service: FixPublishServiceDep,
) -> FixJobResponse:
    """Retry a failed publish with the previously approved strategy."""

    return await fix_publish_service.retry_publish(fix_id, current_user)


@router.post(
    "/fixes/{fix_id}/cancel",
    response_model=FixJobResponse,
    summary="Cancel fix publish",
)
async def cancel_fix_publish(
    fix_id: UUID,
    current_user: CurrentUserDep,
    fix_publish_service: FixPublishServiceDep,
) -> FixJobResponse:
    """Request cancellation for an in-flight publish job."""

    return await fix_publish_service.cancel_publish(fix_id, current_user)


@router.get(
    "/fixes/{fix_id}/audit",
    response_model=list[FixAuditLogResponse],
    summary="List fix audit events",
)
async def list_fix_audit_events(
    fix_id: UUID,
    current_user: CurrentUserDep,
    fix_publish_service: FixPublishServiceDep,
) -> list[FixAuditLogResponse]:
    """Return the owner-scoped audit timeline for a fix job."""

    return await fix_publish_service.list_audit_logs(fix_id, current_user)


@router.get(
    "/fixes/{fix_id}/stream",
    response_class=StreamingResponse,
    summary="Stream fix job progress",
)
async def stream_fix_job_progress_route(
    fix_id: UUID,
    request: Request,
    current_user: CurrentUserDep,
    fix_job_service: FixJobServiceDep,
    redis_client: RedisDep,
) -> StreamingResponse:
    """Stream owner-scoped fix progress from an isolated Redis channel."""

    fallback_event = await fix_job_service.get_progress_snapshot(
        fix_id,
        current_user,
    )
    return StreamingResponse(
        stream_fix_job_progress(
            fix_id=fix_id,
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
