"""Tests for review job lifecycle service actions."""

from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.models.review_job import ReviewJobStatus
from app.models.user import User
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.services.review_jobs.queue import JobQueueService
from app.services.review_jobs.service import ReviewJobService


@pytest.mark.asyncio
async def test_delete_completed_job_does_not_cancel_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    job_id = uuid4()
    job = build_job(job_id, user_id, ReviewJobStatus.COMPLETED)
    job_repository = FakeReviewJobRepository(job)
    queue_service = FakeJobQueueService()
    published_events: list[object] = []

    async def publish_job_progress(*args: object, **kwargs: object) -> int:
        published_events.append((args, kwargs))
        return 1

    monkeypatch.setattr(
        "app.services.review_jobs.service.publish_job_progress",
        publish_job_progress,
    )

    service = build_service(job_repository, queue_service)

    await service.delete_job(job_id, build_user(user_id))

    assert job_repository.deleted_job_id == job_id
    assert queue_service.canceled_job_ids == []
    assert published_events == []


@pytest.mark.asyncio
async def test_delete_active_job_cancels_queue_and_publishes_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    job_id = uuid4()
    job = build_job(job_id, user_id, ReviewJobStatus.AI_REVIEWING)
    job_repository = FakeReviewJobRepository(job)
    queue_service = FakeJobQueueService()
    published_events: list[tuple[UUID, str, dict[str, object]]] = []

    async def publish_job_progress(
        published_job_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> int:
        published_events.append((published_job_id, event_type, payload))
        return 1

    monkeypatch.setattr(
        "app.services.review_jobs.service.publish_job_progress",
        publish_job_progress,
    )

    service = build_service(job_repository, queue_service)

    await service.delete_job(job_id, build_user(user_id))

    assert job_repository.deleted_job_id == job_id
    assert queue_service.canceled_job_ids == [job_id]
    assert published_events == [
        (
            job_id,
            "failed",
            {
                "status": ReviewJobStatus.FAILED.value,
                "progress": 100,
                "message": "Review job deleted",
                "data": {"reason": "deleted"},
            },
        )
    ]


def build_service(
    job_repository: "FakeReviewJobRepository",
    queue_service: "FakeJobQueueService",
) -> ReviewJobService:
    return ReviewJobService(
        cast(ReviewJobRepository, job_repository),
        cast(RepositoryRepository, SimpleNamespace()),
        cast(JobQueueService, queue_service),
    )


def build_job(
    job_id: UUID,
    user_id: UUID,
    status: ReviewJobStatus,
) -> SimpleNamespace:
    return SimpleNamespace(id=job_id, user_id=user_id, status=status)


def build_user(user_id: UUID) -> User:
    return cast(User, SimpleNamespace(id=user_id))


class FakeReviewJobRepository:
    def __init__(self, job: SimpleNamespace) -> None:
        self.job = job
        self.deleted_job_id: UUID | None = None
        self.rolled_back = False

    async def get_by_id(self, job_id: UUID) -> SimpleNamespace | None:
        if self.job.id != job_id:
            return None

        return self.job

    async def delete(self, review_job: SimpleNamespace) -> None:
        self.deleted_job_id = review_job.id

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeJobQueueService:
    def __init__(self) -> None:
        self.canceled_job_ids: list[UUID] = []

    async def cancel(self, job_id: UUID) -> None:
        self.canceled_job_ids.append(job_id)
