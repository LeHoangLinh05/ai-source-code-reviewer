"""Opt-in authentication performance smoke tests."""

import asyncio
import os
import time
from statistics import quantiles

import httpx
import pytest

AUTH_PERFORMANCE_REQUEST_COUNT = 10
AUTH_PERFORMANCE_P95_SECONDS = 1.0
PERCENTILE_COUNT = 20

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(
        os.getenv("RUN_AUTH_PERFORMANCE_TESTS") != "1",
        reason="Set RUN_AUTH_PERFORMANCE_TESTS=1 to run auth performance smoke tests.",
    ),
]


@pytest.mark.asyncio
async def test_auth_login_profile_performance_smoke() -> None:
    base_url = required_env("AUTH_PERFORMANCE_BASE_URL")
    email = required_env("AUTH_PERFORMANCE_EMAIL")
    password = required_env("AUTH_PERFORMANCE_PASSWORD")

    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        timings = await asyncio.gather(
            *(
                login_and_load_profile(client, email, password)
                for _ in range(AUTH_PERFORMANCE_REQUEST_COUNT)
            )
        )

    p95_seconds = quantiles(timings, n=PERCENTILE_COUNT)[-1]
    assert p95_seconds < AUTH_PERFORMANCE_P95_SECONDS


async def login_and_load_profile(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> float:
    started_at = time.perf_counter()
    login_response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    login_response.raise_for_status()
    access_token = login_response.json()["access_token"]
    profile_response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    profile_response.raise_for_status()
    return time.perf_counter() - started_at


def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        pytest.skip(f"{name} is required for auth performance smoke tests.")

    return value
