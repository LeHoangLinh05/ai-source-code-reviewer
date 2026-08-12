"""Redis integration coverage for isolated concurrent job progress channels."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.services.notification_service import (
    build_job_progress_channel,
    publish_job_progress,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_job_streams_do_not_leak_channels() -> None:
    """Each subscribed client receives only the event for its own review job."""

    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set TEST_REDIS_URL to run the real Redis integration test")

    client = Redis.from_url(redis_url, decode_responses=True)
    job_ids = [uuid4() for _ in range(3)]
    pubsubs = [client.pubsub() for _ in job_ids]
    try:
        await asyncio.gather(
            *(
                pubsub.subscribe(build_job_progress_channel(job_id))
                for pubsub, job_id in zip(pubsubs, job_ids, strict=True)
            )
        )
        await asyncio.gather(
            *(
                publish_job_progress(
                    job_id,
                    "progress_update",
                    {
                        "status": "AI_REVIEWING",
                        "progress": index + 1,
                        "message": f"Job {index + 1}",
                    },
                    client,
                )
                for index, job_id in enumerate(job_ids)
            )
        )

        messages = await asyncio.gather(*(_read_message(pubsub) for pubsub in pubsubs))
        received_job_ids = {message["job_id"] for message in messages}
        assert received_job_ids == {str(job_id) for job_id in job_ids}
    finally:
        await asyncio.gather(*(pubsub.aclose() for pubsub in pubsubs))
        await client.aclose()


async def _read_message(pubsub: PubSub) -> dict[str, object]:
    """Read one Redis message while ignoring subscription acknowledgements."""

    for _ in range(20):
        message = await pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=2.0,
        )
        if message is not None and message.get("type") == "message":
            return json.loads(message["data"])

    raise AssertionError("Timed out waiting for Redis progress event")
