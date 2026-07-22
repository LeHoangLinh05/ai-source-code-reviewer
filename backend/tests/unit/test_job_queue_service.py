"""Tests for review job queue dispatch and cancellation."""

from uuid import uuid4

import pytest

from app.services import job_queue_service
from app.services.job_queue_service import JobQueueService


@pytest.mark.asyncio
async def test_enqueue_uses_job_id_as_celery_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    fake_task = FakeReviewTask()
    monkeypatch.setattr(job_queue_service, "process_review_job", fake_task)

    await JobQueueService().enqueue(job_id)

    assert fake_task.applied_args == [str(job_id)]
    assert fake_task.applied_task_id == str(job_id)


@pytest.mark.asyncio
async def test_cancel_revokes_job_id_task(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = uuid4()
    fake_task = FakeReviewTask()
    monkeypatch.setattr(job_queue_service, "process_review_job", fake_task)

    await JobQueueService().cancel(job_id)

    assert fake_task.result.task_id == str(job_id)
    assert fake_task.result.terminate is True
    assert fake_task.result.signal == "SIGTERM"


class FakeReviewTask:
    def __init__(self) -> None:
        self.applied_args: list[str] | None = None
        self.applied_task_id: str | None = None
        self.result = FakeAsyncResult()

    def apply_async(self, *, args: list[str], task_id: str) -> object:
        self.applied_args = args
        self.applied_task_id = task_id
        return type("AsyncResult", (), {"id": task_id})()

    def AsyncResult(self, task_id: str) -> "FakeAsyncResult":
        self.result.task_id = task_id
        return self.result


class FakeAsyncResult:
    def __init__(self) -> None:
        self.task_id: str | None = None
        self.terminate: bool | None = None
        self.signal: str | None = None

    def revoke(self, *, terminate: bool, signal: str) -> None:
        self.terminate = terminate
        self.signal = signal
