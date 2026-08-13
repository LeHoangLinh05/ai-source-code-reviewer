"""Publish and stream realtime review job progress."""

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
from app.schemas.notification import JobProgressEvent, JobProgressEventType
from app.services.realtime_progress_errors import is_realtime_progress_error

JOB_PROGRESS_EVENTS: set[JobProgressEventType] = {
    "status_change",
    "progress_update",
    "log",
    "completed",
    "failed",
}
JOB_PROGRESS_CHANNEL_PREFIX = "job"
JOB_PROGRESS_SNAPSHOT_SUFFIX = "last"
JOB_PROGRESS_SNAPSHOT_TTL_SECONDS = 60 * 60
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SSE_MESSAGE_POLL_SECONDS = 1.0
SSE_RETRY_MILLISECONDS = 3_000

logger = logging.getLogger(__name__)


async def publish_job_progress(
    job_id: UUID,
    event_type: str,
    data: dict[str, object],
    redis_client: Redis | None = None,
) -> int:
    """Publish one normalized job progress event to the Redis channel."""

    if event_type not in JOB_PROGRESS_EVENTS:
        raise ValueError(f"Unsupported job progress event: {event_type}")

    progress_event = JobProgressEvent(
        job_id=job_id,
        event=cast(JobProgressEventType, event_type),
        status=str(data.get("status", "")),
        progress=_get_progress(data),
        message=str(data.get("message", "")),
        timestamp=datetime.now(UTC),
        data=_get_event_data(data),
    )
    serialized_event = progress_event.model_dump_json()
    publisher = redis_client or get_redis_client()
    try:
        await publisher.set(
            build_job_progress_snapshot_key(job_id),
            serialized_event,
            ex=JOB_PROGRESS_SNAPSHOT_TTL_SECONDS,
        )
        return int(
            await publisher.publish(
                build_job_progress_channel(job_id),
                serialized_event,
            )
        )
    except (RedisError, RuntimeError) as error:
        if not is_realtime_progress_error(error):
            raise

        logger.warning(
            "Unable to publish realtime progress for review job %s",
            job_id,
            exc_info=True,
        )
        return 0


async def stream_job_progress(
    *,
    job_id: UUID,
    fallback_event: JobProgressEvent,
    request: Request,
    redis_client: Redis | None = None,
) -> AsyncIterator[str]:
    """Yield a reconnect-safe SSE stream for one review job."""

    client = redis_client or get_redis_client()
    pubsub = client.pubsub()
    channel = build_job_progress_channel(job_id)
    try:
        await pubsub.subscribe(channel)
        yield f"retry: {SSE_RETRY_MILLISECONDS}\n\n"

        snapshot = await client.get(build_job_progress_snapshot_key(job_id))
        initial_event = _parse_progress_event(snapshot) or fallback_event
        yield format_sse_event(initial_event)
        if initial_event.is_terminal:
            return

        last_heartbeat = time.monotonic()
        while not await request.is_disconnected():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=SSE_MESSAGE_POLL_SECONDS,
            )
            if message is not None and message.get("type") == "message":
                progress_event = _parse_progress_event(message.get("data"))
                if progress_event is not None:
                    yield format_sse_event(progress_event)
                    if progress_event.is_terminal:
                        return

            now = time.monotonic()
            if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            await asyncio.sleep(0)
    except RedisError:
        logger.warning(
            "Realtime progress stream interrupted for review job %s",
            job_id,
            exc_info=True,
        )
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()


def format_sse_event(progress_event: JobProgressEvent) -> str:
    """Serialize one progress event using the SSE wire format."""

    event_id = progress_event.timestamp.isoformat()
    return (
        f"id: {event_id}\n"
        f"event: {progress_event.event}\n"
        f"data: {progress_event.model_dump_json()}\n\n"
    )


def build_job_progress_channel(job_id: UUID) -> str:
    """Return the isolated Redis channel for one review job."""

    return f"{JOB_PROGRESS_CHANNEL_PREFIX}:{job_id}:progress"


def build_job_progress_snapshot_key(job_id: UUID) -> str:
    """Return the reconnect snapshot key for one review job."""

    return f"{build_job_progress_channel(job_id)}:{JOB_PROGRESS_SNAPSHOT_SUFFIX}"


def _parse_progress_event(value: object) -> JobProgressEvent | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        return None

    try:
        return JobProgressEvent.model_validate_json(value)
    except ValueError:
        logger.warning("Ignoring malformed job progress event")
        return None


def _get_event_data(data: dict[str, object]) -> dict[str, object]:
    event_data = data.get("data")
    if isinstance(event_data, dict):
        return event_data

    return {}


def _get_progress(data: dict[str, object]) -> int:
    progress = data.get("progress", 0)
    if isinstance(progress, int):
        return progress

    if isinstance(progress, str):
        return int(progress)

    return 0
