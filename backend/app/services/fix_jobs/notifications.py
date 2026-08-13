"""Publish and stream realtime fix job progress."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.db.redis import get_redis_client
from app.models.fix_job import FixJob, FixJobStatus, FixPublishStatus
from app.schemas.fix_job import (
    FixJobProgressEvent,
    FixJobProgressEventType,
    normalize_fix_validation_result,
)
from app.services.realtime_progress_errors import is_realtime_progress_error

FIX_JOB_PROGRESS_EVENTS: set[FixJobProgressEventType] = {
    "status_change",
    "progress_update",
    "log",
    "completed",
    "failed",
}
FIX_JOB_PROGRESS_CHANNEL_PREFIX = "fix_job"
FIX_JOB_PROGRESS_SNAPSHOT_SUFFIX = "last"
FIX_JOB_PROGRESS_SNAPSHOT_TTL_SECONDS = 60 * 60
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_MESSAGE_POLL_SECONDS = 1.0
SSE_RETRY_MILLISECONDS = 3_000

FIX_JOB_PROGRESS_BY_STATUS: dict[FixJobStatus, int] = {
    FixJobStatus.PENDING: 0,
    FixJobStatus.PREPARING: 10,
    FixJobStatus.GENERATING_PATCH: 45,
    FixJobStatus.VALIDATING: 80,
    FixJobStatus.WAITING_APPROVAL: 100,
    FixJobStatus.APPROVED: 100,
    FixJobStatus.FAILED: 100,
}
FIX_JOB_PROGRESS_MESSAGES: dict[FixJobStatus, str] = {
    FixJobStatus.PENDING: "Fix job queued.",
    FixJobStatus.PREPARING: "Preparing the fix sandbox.",
    FixJobStatus.GENERATING_PATCH: "Generating the selected code changes.",
    FixJobStatus.VALIDATING: "Running sandbox validation.",
    FixJobStatus.WAITING_APPROVAL: "Patch is ready for review.",
    FixJobStatus.APPROVED: "Patch approved.",
    FixJobStatus.FAILED: "Fix job failed.",
}
NON_TERMINAL_PUBLISH_STATUSES = {
    FixPublishStatus.PUBLISHING,
    FixPublishStatus.FAILED,
    FixPublishStatus.NEEDS_FORK,
    FixPublishStatus.STALE_BASE,
}

logger = logging.getLogger(__name__)


async def publish_fix_job_progress(
    fix_job: FixJob,
    *,
    message: str | None = None,
    data: dict[str, object] | None = None,
    redis_client: Redis | None = None,
) -> int:
    """Publish one normalized fix job progress event to Redis."""

    progress_event = build_fix_job_progress_event(
        fix_job,
        message=message,
        data=data,
        timestamp=datetime.now(UTC),
    )
    serialized_event = progress_event.model_dump_json()
    publisher = redis_client or get_redis_client()
    try:
        await publisher.set(
            build_fix_job_progress_snapshot_key(fix_job.id),
            serialized_event,
            ex=FIX_JOB_PROGRESS_SNAPSHOT_TTL_SECONDS,
        )
        return int(
            await publisher.publish(
                build_fix_job_progress_channel(fix_job.id),
                serialized_event,
            )
        )
    except (RedisError, RuntimeError) as error:
        if not is_realtime_progress_error(error):
            raise

        logger.warning(
            "Unable to publish realtime progress for fix job %s",
            fix_job.id,
            exc_info=True,
        )
        return 0


async def stream_fix_job_progress(
    *,
    fix_id: UUID,
    fallback_event: FixJobProgressEvent,
    request: Request,
    redis_client: Redis | None = None,
) -> AsyncIterator[str]:
    """Yield a reconnect-safe SSE stream for one fix job."""

    client = redis_client or get_redis_client()
    pubsub = client.pubsub()
    channel = build_fix_job_progress_channel(fix_id)
    try:
        await pubsub.subscribe(channel)
        yield f"retry: {SSE_RETRY_MILLISECONDS}\n\n"

        snapshot = await client.get(build_fix_job_progress_snapshot_key(fix_id))
        initial_event = _parse_fix_job_progress_event(snapshot) or fallback_event
        yield format_fix_job_sse_event(initial_event)
        if initial_event.is_terminal:
            return

        last_heartbeat = time.monotonic()
        while not await request.is_disconnected():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=SSE_MESSAGE_POLL_SECONDS,
            )
            if message is not None and message.get("type") == "message":
                progress_event = _parse_fix_job_progress_event(message.get("data"))
                if progress_event is not None:
                    yield format_fix_job_sse_event(progress_event)
                    if progress_event.is_terminal:
                        return

            now = time.monotonic()
            if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            await asyncio.sleep(0)
    except RedisError:
        logger.warning(
            "Realtime progress stream interrupted for fix job %s",
            fix_id,
            exc_info=True,
        )
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()


def build_fix_job_progress_event(
    fix_job: FixJob,
    *,
    message: str | None = None,
    data: dict[str, object] | None = None,
    timestamp: datetime,
) -> FixJobProgressEvent:
    """Build a typed progress event from the current fix job state."""

    event_data = _build_fix_job_event_data(fix_job)
    if data is not None:
        event_data.update(data)

    publish_status = fix_job.publish_status or FixPublishStatus.NOT_REQUESTED

    return FixJobProgressEvent(
        fix_id=fix_job.id,
        review_job_id=fix_job.review_job_id,
        event=_get_progress_event_type(
            fix_job.status,
            publish_status=publish_status,
        ),
        status=fix_job.status,
        progress=FIX_JOB_PROGRESS_BY_STATUS[fix_job.status],
        message=message or FIX_JOB_PROGRESS_MESSAGES[fix_job.status],
        timestamp=timestamp,
        data=event_data,
    )


def format_fix_job_sse_event(progress_event: FixJobProgressEvent) -> str:
    """Serialize one fix progress event using the SSE wire format."""

    event_id = progress_event.timestamp.isoformat()
    return (
        f"id: {event_id}\n"
        f"event: {progress_event.event}\n"
        f"data: {progress_event.model_dump_json()}\n\n"
    )


def build_fix_job_progress_channel(fix_id: UUID) -> str:
    """Return the isolated Redis channel for one fix job."""

    return f"{FIX_JOB_PROGRESS_CHANNEL_PREFIX}:{fix_id}:progress"


def build_fix_job_progress_snapshot_key(fix_id: UUID) -> str:
    """Return the reconnect snapshot key for one fix job."""

    return (
        f"{build_fix_job_progress_channel(fix_id)}:{FIX_JOB_PROGRESS_SNAPSHOT_SUFFIX}"
    )


def _build_fix_job_event_data(fix_job: FixJob) -> dict[str, object]:
    publish_status = fix_job.publish_status or FixPublishStatus.NOT_REQUESTED
    validation_summary = normalize_fix_validation_result(
        validation_status=fix_job.validation_status,
        validation_output=fix_job.validation_output,
    )
    data: dict[str, object] = {
        "base_commit_sha": fix_job.base_commit_sha,
        "changed_files": fix_job.changed_files or [],
        "failure_reason": fix_job.error_message,
        "fix_branch": fix_job.fix_branch,
        "issue_ids": fix_job.issue_ids,
        "issue_plan": fix_job.issue_plan or [],
        "issue_results": fix_job.issue_results or [],
        "pr_url": fix_job.pr_url,
        "provider": fix_job.provider.value if fix_job.provider is not None else None,
        "publish_error": fix_job.publish_error,
        "publish_status": publish_status.value,
        "published_branch": fix_job.published_branch,
        "published_commit_sha": fix_job.published_commit_sha,
        "validation_status": fix_job.validation_status.value,
    }
    if validation_summary is not None:
        data["validation_summary"] = cast(
            dict[str, object],
            validation_summary.model_dump(mode="json"),
        )

    return data


def _get_progress_event_type(
    status: FixJobStatus,
    *,
    publish_status: FixPublishStatus,
) -> FixJobProgressEventType:
    if status == FixJobStatus.FAILED:
        return "failed"

    if status == FixJobStatus.WAITING_APPROVAL and (
        publish_status in NON_TERMINAL_PUBLISH_STATUSES
    ):
        return "status_change"

    if status in {FixJobStatus.WAITING_APPROVAL, FixJobStatus.APPROVED}:
        return "completed"

    return "status_change"


def _parse_fix_job_progress_event(value: object) -> FixJobProgressEvent | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return None

    try:
        return FixJobProgressEvent.model_validate_json(value)
    except ValueError:
        logger.warning("Ignoring malformed fix job progress event")
        return None
