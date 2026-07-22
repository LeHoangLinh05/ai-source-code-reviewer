"""Notification and realtime progress streaming workflows."""

import json
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

from app.db.redis import get_redis_client

JOB_PROGRESS_EVENTS = {
    "status_change",
    "progress_update",
    "log",
    "completed",
    "failed",
}


async def publish_job_progress(
    job_id: UUID,
    event_type: str,
    data: dict[str, object],
    redis_client: Redis | None = None,
) -> int:
    """Publish one normalized job progress event to the Redis channel."""

    if event_type not in JOB_PROGRESS_EVENTS:
        raise ValueError(f"Unsupported job progress event: {event_type}")

    progress_payload = {
        "job_id": str(job_id),
        "event": event_type,
        "status": str(data.get("status", "")),
        "progress": _get_progress(data),
        "message": str(data.get("message", "")),
        "timestamp": datetime.now(UTC).isoformat(),
        "data": _get_event_data(data),
    }
    publisher = redis_client or get_redis_client()
    return int(
        await publisher.publish(
            f"job:{job_id}:progress",
            json.dumps(progress_payload, default=str),
        )
    )


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
