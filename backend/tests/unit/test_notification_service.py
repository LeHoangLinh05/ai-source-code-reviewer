"""Tests for Redis job progress publishing."""

import json
from uuid import uuid4

import pytest

from app.services.notification_service import publish_job_progress


class FakeRedisPublisher:
    """Minimal Redis publisher fake."""

    def __init__(self) -> None:
        self.channel: str | None = None
        self.message: str | None = None

    async def publish(self, channel: str, message: str) -> int:
        self.channel = channel
        self.message = message
        return 1


@pytest.mark.asyncio
async def test_publish_job_progress_uses_phase_5_payload_contract() -> None:
    redis_client = FakeRedisPublisher()
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
        redis_client,  # type: ignore[arg-type]
    )

    assert subscriber_count == 1
    assert redis_client.channel == f"job:{job_id}:progress"
    assert redis_client.message is not None
    payload = json.loads(redis_client.message)
    assert payload["job_id"] == str(job_id)
    assert payload["event"] == "status_change"
    assert payload["status"] == "CLONING"
    assert payload["progress"] == 10
    assert payload["message"] == "Cloning repository"
    assert payload["data"] == {"current_file": "README.md"}
    assert isinstance(payload["timestamp"], str)
