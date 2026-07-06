"""Tests for review-job rate limiting dependency."""

from uuid import uuid4

import pytest

from app.core.dependencies import (
    RATE_LIMIT_WINDOW_SECONDS,
    REVIEW_JOB_CREATE_RATE_LIMIT,
    rate_limit_review_job_create,
)
from app.core.exceptions import RateLimitError


class FakeUser:
    """Small authenticated-user stand-in for dependency tests."""

    def __init__(self) -> None:
        self.id = uuid4()


class FakeRedisCounter:
    """Minimal Redis counter fake for rate-limit tests."""

    def __init__(self, count: int, ttl_seconds: int = 42) -> None:
        self.count = count
        self.ttl_seconds = ttl_seconds
        self.expired_key: str | None = None

    async def incr(self, _key: str) -> int:
        return self.count

    async def expire(self, key: str, seconds: int) -> bool:
        self.expired_key = f"{key}:{seconds}"
        return True

    async def ttl(self, _key: str) -> int:
        return self.ttl_seconds


@pytest.mark.asyncio
async def test_rate_limit_allows_requests_under_limit() -> None:
    redis_client = FakeRedisCounter(count=REVIEW_JOB_CREATE_RATE_LIMIT)

    await rate_limit_review_job_create(
        FakeUser(),  # type: ignore[arg-type]
        redis_client,  # type: ignore[arg-type]
    )

    assert redis_client.expired_key is None


@pytest.mark.asyncio
async def test_rate_limit_sets_ttl_for_first_request() -> None:
    redis_client = FakeRedisCounter(count=1)
    user = FakeUser()

    await rate_limit_review_job_create(
        user,  # type: ignore[arg-type]
        redis_client,  # type: ignore[arg-type]
    )

    expected_key = f"rate:{user.id}:review_jobs:create:{RATE_LIMIT_WINDOW_SECONDS}"
    assert redis_client.expired_key == expected_key


@pytest.mark.asyncio
async def test_rate_limit_rejects_requests_over_limit() -> None:
    redis_client = FakeRedisCounter(count=REVIEW_JOB_CREATE_RATE_LIMIT + 1)

    with pytest.raises(RateLimitError) as error:
        await rate_limit_review_job_create(
            FakeUser(),  # type: ignore[arg-type]
            redis_client,  # type: ignore[arg-type]
        )

    assert error.value.headers == {"Retry-After": "42"}
