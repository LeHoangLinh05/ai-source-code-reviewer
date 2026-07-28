"""Tests for Redis-backed job progress publishing and SSE streaming."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.main import app
from app.schemas.notification import JobProgressEvent
from app.services import notification_service
from app.services.notification_service import (
    JOB_PROGRESS_SNAPSHOT_TTL_SECONDS,
    build_job_progress_channel,
    build_job_progress_snapshot_key,
    publish_job_progress,
    stream_job_progress,
)


class FakePubSub:
    """In-memory Redis Pub/Sub fake with observable cleanup."""

    def __init__(self, messages: list[dict[str, object]] | None = None) -> None:
        self.messages = messages or []
        self.subscribed_channel: str | None = None
        self.unsubscribed_channel: str | None = None
        self.is_closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed_channel = channel

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed_channel = channel

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,
    ) -> dict[str, object] | None:
        del ignore_subscribe_messages, timeout
        if not self.messages:
            return None
        return self.messages.pop(0)

    async def aclose(self) -> None:
        self.is_closed = True


class FakeRedis:
    """Minimal Redis fake for progress snapshot and channel behavior."""

    def __init__(
        self,
        *,
        snapshot: str | bytes | None = None,
        messages: list[dict[str, object]] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.pubsub_client = FakePubSub(messages)
        self.snapshot_key: str | None = None
        self.snapshot_value: str | None = None
        self.snapshot_ttl: int | None = None
        self.channel: str | None = None
        self.message: str | None = None

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
    ) -> bool:
        self.snapshot_key = key
        self.snapshot_value = value
        self.snapshot_ttl = ex
        return True

    async def get(self, key: str) -> str | bytes | None:
        self.snapshot_key = key
        return self.snapshot

    async def publish(self, channel: str, message: str) -> int:
        self.channel = channel
        self.message = message
        return 1

    def pubsub(self) -> FakePubSub:
        return self.pubsub_client


class FailingRedis(FakeRedis):
    """Redis fake that simulates an unavailable realtime backend."""

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
    ) -> bool:
        del key, value, ex
        raise RedisError("unavailable")


class FakeRequest:
    """Request fake that disconnects after a configured number of checks."""

    def __init__(self, disconnect_results: list[bool]) -> None:
        self._disconnect_results = iter(disconnect_results)

    async def is_disconnected(self) -> bool:
        return next(self._disconnect_results, True)


def build_event(
    job_id: UUID,
    *,
    event: str = "status_change",
    status: str = "CLONING",
    progress: int = 10,
) -> JobProgressEvent:
    """Build one deterministic event fixture."""

    return JobProgressEvent.model_validate(
        {
            "job_id": job_id,
            "event": event,
            "status": status,
            "progress": progress,
            "message": f"Progress: {progress}",
            "timestamp": datetime(2026, 7, 28, tzinfo=UTC),
            "data": {},
        }
    )


def monotonic_values(*values: float) -> Iterator[float]:
    """Return a finite monotonic clock fixture."""

    return iter(values)


@pytest.mark.asyncio
async def test_publish_job_progress_stores_snapshot_before_publishing() -> None:
    redis_client = FakeRedis()
    job_id = uuid4()

    subscriber_count = await publish_job_progress(
        job_id,
        "status_change",
        {
            "status": "CLONING",
            "progress": 10,
            "message": "Cloning repository",
            "data": {"current_file": "README.md"},
        },
        cast(Redis, redis_client),
    )

    assert subscriber_count == 1
    assert redis_client.snapshot_key == build_job_progress_snapshot_key(job_id)
    assert redis_client.snapshot_ttl == JOB_PROGRESS_SNAPSHOT_TTL_SECONDS
    assert redis_client.snapshot_value == redis_client.message
    assert redis_client.channel == build_job_progress_channel(job_id)
    assert redis_client.message is not None
    payload = json.loads(redis_client.message)
    assert payload["job_id"] == str(job_id)
    assert payload["event"] == "status_change"
    assert payload["status"] == "CLONING"
    assert payload["progress"] == 10
    assert payload["message"] == "Cloning repository"
    assert payload["data"] == {"current_file": "README.md"}
    assert isinstance(payload["timestamp"], str)


@pytest.mark.asyncio
async def test_publish_job_progress_rejects_unknown_event() -> None:
    with pytest.raises(ValueError, match="Unsupported job progress event"):
        await publish_job_progress(
            uuid4(),
            "unknown",
            {},
            cast(Redis, FakeRedis()),
        )


@pytest.mark.asyncio
async def test_publish_failure_does_not_raise_after_business_commit() -> None:
    result = await publish_job_progress(
        uuid4(),
        "failed",
        {
            "status": "FAILED",
            "progress": 100,
            "message": "Review failed",
        },
        cast(Redis, FailingRedis()),
    )

    assert result == 0


@pytest.mark.asyncio
async def test_stream_replays_snapshot_and_closes_on_terminal_event() -> None:
    job_id = uuid4()
    fallback_event = build_event(job_id)
    terminal_event = build_event(
        job_id,
        event="completed",
        status="COMPLETED",
        progress=100,
    )
    redis_client = FakeRedis(snapshot=terminal_event.model_dump_json())

    chunks = [
        chunk
        async for chunk in stream_job_progress(
            job_id=job_id,
            fallback_event=fallback_event,
            request=cast(Request, FakeRequest([False])),
            redis_client=cast(Redis, redis_client),
        )
    ]

    assert chunks[0].startswith("retry: ")
    assert "event: completed" in chunks[1]
    assert "Progress: 100" in chunks[1]
    assert redis_client.pubsub_client.subscribed_channel == (
        build_job_progress_channel(job_id)
    )
    assert redis_client.pubsub_client.unsubscribed_channel == (
        build_job_progress_channel(job_id)
    )
    assert redis_client.pubsub_client.is_closed


@pytest.mark.asyncio
async def test_stream_uses_history_fallback_then_forwards_named_events() -> None:
    job_id = uuid4()
    fallback_event = build_event(job_id)
    terminal_event = build_event(
        job_id,
        event="failed",
        status="FAILED",
        progress=100,
    )
    redis_client = FakeRedis(
        messages=[
            {
                "type": "message",
                "data": terminal_event.model_dump_json().encode(),
            }
        ]
    )

    chunks = [
        chunk
        async for chunk in stream_job_progress(
            job_id=job_id,
            fallback_event=fallback_event,
            request=cast(Request, FakeRequest([False, False])),
            redis_client=cast(Redis, redis_client),
        )
    ]

    assert "event: status_change" in chunks[1]
    assert "event: failed" in chunks[2]
    assert redis_client.pubsub_client.is_closed


@pytest.mark.asyncio
async def test_stream_sends_heartbeat_and_cleans_up_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    redis_client = FakeRedis()
    clock = monotonic_values(0.0, 15.0)
    monkeypatch.setattr(
        notification_service,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )

    chunks = [
        chunk
        async for chunk in stream_job_progress(
            job_id=job_id,
            fallback_event=build_event(job_id),
            request=cast(Request, FakeRequest([False, True])),
            redis_client=cast(Redis, redis_client),
        )
    ]

    assert chunks[-1] == ": keep-alive\n\n"
    assert redis_client.pubsub_client.is_closed


def test_sse_router_is_registered_without_admin_routes() -> None:
    paths = set(app.openapi()["paths"])

    assert "/api/review-jobs/{job_id}/stream" in paths
    assert not any(path.startswith("/api/admin") for path in paths)
