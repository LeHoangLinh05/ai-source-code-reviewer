"""Tests for aggregate infrastructure health checks."""

import pytest

from app.services.health_service import HealthService


class FakeSession:
    """Minimal SQLAlchemy session fake."""

    async def execute(self, _statement: object) -> None:
        return None


class FakeRedis:
    """Minimal Redis fake."""

    async def ping(self) -> bool:
        return True


class FakeMongoDatabase:
    """Minimal MongoDB database fake."""

    async def command(self, command_name: str) -> dict[str, int]:
        assert command_name == "ping"
        return {"ok": 1}


@pytest.mark.asyncio
async def test_health_service_includes_mongodb() -> None:
    health = await HealthService(
        FakeSession(),  # type: ignore[arg-type]
        FakeRedis(),  # type: ignore[arg-type]
        FakeMongoDatabase(),  # type: ignore[arg-type]
    ).check()

    assert health.status == "ok"
    assert set(health.services) == {"postgres", "redis", "mongodb"}
    assert health.services["mongodb"].status == "ok"
