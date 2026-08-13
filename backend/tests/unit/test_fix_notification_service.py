"""Tests for Redis-backed fix job progress publishing and streaming."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models.fix_job import FixJob, FixJobStatus, FixValidationStatus
from app.services.fix_jobs import notifications as fix_notification_service
from app.services.fix_jobs.notifications import (
    FIX_JOB_PROGRESS_SNAPSHOT_TTL_SECONDS,
    build_fix_job_progress_channel,
    build_fix_job_progress_event,
    build_fix_job_progress_snapshot_key,
    publish_fix_job_progress,
    stream_fix_job_progress,
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
    """Minimal Redis fake for fix progress snapshot and channel behavior."""

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
    """Redis fake that simulates unavailable realtime publishing."""

    def __init__(self, error: RedisError | RuntimeError) -> None:
        super().__init__()
        self.error = error

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
    ) -> bool:
        del key, value, ex
        raise self.error


class FakeRequest:
    """Request fake that disconnects after a configured number of checks."""

    def __init__(self, disconnect_results: list[bool]) -> None:
        self._disconnect_results = iter(disconnect_results)

    async def is_disconnected(self) -> bool:
        return next(self._disconnect_results, True)


def monotonic_values(*values: float) -> Iterator[float]:
    """Return a finite monotonic clock fixture."""

    return iter(values)


@pytest.mark.asyncio
async def test_publish_fix_job_progress_stores_snapshot_before_publishing() -> None:
    redis_client = FakeRedis()
    fix_job = build_fix_job(status=FixJobStatus.VALIDATING)

    subscriber_count = await publish_fix_job_progress(
        fix_job,
        redis_client=cast(Redis, redis_client),
    )

    assert subscriber_count == 1
    assert redis_client.snapshot_key == build_fix_job_progress_snapshot_key(fix_job.id)
    assert redis_client.snapshot_ttl == FIX_JOB_PROGRESS_SNAPSHOT_TTL_SECONDS
    assert redis_client.snapshot_value == redis_client.message
    assert redis_client.channel == build_fix_job_progress_channel(fix_job.id)
    assert redis_client.message is not None
    payload = json.loads(redis_client.message)
    assert payload["fix_id"] == str(fix_job.id)
    assert payload["review_job_id"] == str(fix_job.review_job_id)
    assert payload["status"] == "VALIDATING"
    assert payload["progress"] == 80
    assert payload["data"]["changed_files"] == ["src/app.py"]


@pytest.mark.asyncio
async def test_publish_fix_job_progress_suppresses_redis_failure() -> None:
    fix_job = build_fix_job(status=FixJobStatus.FAILED)

    subscriber_count = await publish_fix_job_progress(
        fix_job,
        redis_client=cast(Redis, FailingRedis(RedisError("unavailable"))),
    )

    assert subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_fix_job_progress_suppresses_closed_event_loop() -> None:
    fix_job = build_fix_job(status=FixJobStatus.FAILED)

    subscriber_count = await publish_fix_job_progress(
        fix_job,
        redis_client=cast(Redis, FailingRedis(RuntimeError("Event loop is closed"))),
    )

    assert subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_fix_job_progress_raises_unexpected_runtime_error() -> None:
    fix_job = build_fix_job(status=FixJobStatus.FAILED)

    with pytest.raises(RuntimeError, match="different failure"):
        await publish_fix_job_progress(
            fix_job,
            redis_client=cast(Redis, FailingRedis(RuntimeError("different failure"))),
        )


@pytest.mark.asyncio
async def test_stream_replays_ready_snapshot_and_closes() -> None:
    fix_job = build_fix_job(status=FixJobStatus.WAITING_APPROVAL)
    fallback_event = build_fix_job_progress_event(
        fix_job,
        timestamp=datetime(2026, 7, 30, tzinfo=UTC),
    )
    redis_client = FakeRedis(snapshot=fallback_event.model_dump_json())

    chunks = [
        chunk
        async for chunk in stream_fix_job_progress(
            fix_id=fix_job.id,
            fallback_event=fallback_event,
            request=cast(Request, FakeRequest([False])),
            redis_client=cast(Redis, redis_client),
        )
    ]

    assert chunks[0].startswith("retry: ")
    assert "event: completed" in chunks[1]
    assert "Patch is ready for review." in chunks[1]
    assert redis_client.pubsub_client.subscribed_channel == (
        build_fix_job_progress_channel(fix_job.id)
    )
    assert redis_client.pubsub_client.unsubscribed_channel == (
        build_fix_job_progress_channel(fix_job.id)
    )
    assert redis_client.pubsub_client.is_closed


@pytest.mark.asyncio
async def test_stream_sends_fix_heartbeat_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job(status=FixJobStatus.PREPARING)
    redis_client = FakeRedis()
    clock = monotonic_values(0.0, 15.0)
    monkeypatch.setattr(
        fix_notification_service,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )

    chunks = [
        chunk
        async for chunk in stream_fix_job_progress(
            fix_id=fix_job.id,
            fallback_event=build_fix_job_progress_event(
                fix_job,
                timestamp=datetime(2026, 7, 30, tzinfo=UTC),
            ),
            request=cast(Request, FakeRequest([False, True])),
            redis_client=cast(Redis, redis_client),
        )
    ]

    assert chunks[-1] == ": keep-alive\n\n"
    assert redis_client.pubsub_client.is_closed


def build_fix_job(*, status: FixJobStatus) -> FixJob:
    """Build one deterministic fix job fixture."""

    return FixJob(
        id=uuid4(),
        review_job_id=uuid4(),
        user_id=uuid4(),
        status=status,
        validation_status=FixValidationStatus.PASSED,
        issue_ids=[str(uuid4())],
        target_branch="main",
        base_commit_sha="a" * 40,
        fix_branch=f"repoguard/fix/{uuid4()}",
        error_message=None,
        diff="diff --git a/src/app.py b/src/app.py",
        changed_files=["src/app.py"],
        validation_output={
            "status": FixValidationStatus.PASSED.value,
            "summary": "Validation passed: 1 passed, 0 failed, 0 skipped.",
            "checks": [],
        },
        pr_url=None,
        started_at=None,
        completed_at=None,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
    )
