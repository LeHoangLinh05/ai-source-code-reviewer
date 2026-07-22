"""Tests for review worker startup failure handling."""

from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.models.review_job import ReviewJobStatus
from app.repositories.review_job_repository import ReviewJobRepository
from app.workers import review_worker


@pytest.mark.asyncio
async def test_worker_startup_failure_marks_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    repository = _RecordingReviewJobRepository(job_exists=True)
    published_events: list[tuple[UUID, str, dict[str, object]]] = []

    async def publish_job_progress(
        published_job_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> int:
        published_events.append((published_job_id, event_type, payload))
        return 1

    monkeypatch.setattr(review_worker, "publish_job_progress", publish_job_progress)

    await review_worker._mark_worker_startup_failure(
        job_id,
        cast(ReviewJobRepository, repository),
        RuntimeError("embedding dependency failed"),
    )

    assert repository.rolled_back is True
    assert repository.failed_job_id == job_id
    assert repository.error_message == (
        "Review pipeline failed: embedding dependency failed"
    )
    assert repository.progress == 100
    assert published_events == [
        (
            job_id,
            "failed",
            {
                "status": ReviewJobStatus.FAILED.value,
                "progress": 100,
                "message": "Review pipeline failed: embedding dependency failed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_worker_startup_failure_skips_removed_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecordingReviewJobRepository(job_exists=False)
    published_events: list[object] = []

    async def publish_job_progress(*args: object, **kwargs: object) -> int:
        published_events.append((args, kwargs))
        return 1

    monkeypatch.setattr(review_worker, "publish_job_progress", publish_job_progress)

    await review_worker._mark_worker_startup_failure(
        uuid4(),
        cast(ReviewJobRepository, repository),
        RuntimeError("embedding dependency failed"),
    )

    assert repository.rolled_back is True
    assert repository.failed_job_id is None
    assert published_events == []


class _RecordingReviewJobRepository:
    def __init__(self, *, job_exists: bool) -> None:
        self.job_exists = job_exists
        self.rolled_back = False
        self.failed_job_id: UUID | None = None
        self.error_message: str | None = None
        self.progress: int | None = None

    async def rollback(self) -> None:
        self.rolled_back = True

    async def get_by_id(self, _job_id: UUID) -> object | None:
        if not self.job_exists:
            return None

        return SimpleNamespace()

    async def mark_failed_by_id(
        self,
        job_id: UUID,
        *,
        error_message: str,
        progress: int,
    ) -> None:
        self.failed_job_id = job_id
        self.error_message = error_message
        self.progress = progress
