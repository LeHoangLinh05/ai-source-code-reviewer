"""Tests for aggregate infrastructure health checks."""

from typing import cast

import pytest

from app.repositories.health_repository import InfrastructureHealthRepository
from app.services.health_service import HealthService


class FakeHealthRepository:
    """Controllable infrastructure health repository fake."""

    def __init__(self, *, is_healthy: bool) -> None:
        self.is_healthy = is_healthy

    async def is_postgres_available(self) -> bool:
        return self.is_healthy

    async def is_redis_available(self) -> bool:
        return self.is_healthy

    async def is_mongodb_available(self) -> bool:
        return self.is_healthy


@pytest.mark.asyncio
async def test_health_service_includes_mongodb() -> None:
    health = await HealthService(
        cast(
            InfrastructureHealthRepository,
            FakeHealthRepository(is_healthy=True),
        ),
    ).check()

    assert health.status == "ok"
    assert set(health.services) == {"postgres", "redis", "mongodb"}
    assert health.services["mongodb"].status == "ok"


@pytest.mark.asyncio
async def test_health_service_reports_aggregate_error() -> None:
    health = await HealthService(
        cast(
            InfrastructureHealthRepository,
            FakeHealthRepository(is_healthy=False),
        ),
    ).check()

    assert health.status == "error"
    assert all(service.status == "error" for service in health.services.values())
