"""Tests for expired sandbox cleanup behavior."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.repositories.review_job_repository import ReviewJobRepository
from app.services.sandbox_cleanup_service import SandboxCleanupService


class FakeReviewJobRepository:
    """Small fake for exercising cleanup behavior without a database."""

    def __init__(self, review_jobs: list[ReviewJob]) -> None:
        self.review_jobs = review_jobs
        self.cleared_job_ids: list[UUID] = []
        self.cutoff: datetime | None = None

    async def list_expired_with_sandbox(self, cutoff: datetime) -> list[ReviewJob]:
        self.cutoff = cutoff
        return self.review_jobs

    async def clear_sandbox_path(self, job_id: UUID) -> None:
        self.cleared_job_ids.append(job_id)


@pytest.mark.asyncio
async def test_cleanup_expired_sandboxes_removes_safe_path(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    job_id = uuid4()
    sandbox_path = sandbox_root / str(job_id)
    sandbox_path.mkdir(parents=True)
    (sandbox_path / "app.py").write_bytes(b"abc")
    (sandbox_path / "nested").mkdir()
    (sandbox_path / "nested" / "module.py").write_bytes(b"de")
    repository = FakeReviewJobRepository([_build_review_job(job_id, sandbox_path)])
    service = SandboxCleanupService(
        settings=Settings(sandbox_root=str(sandbox_root), sandbox_ttl_hours=1),
        review_job_repository=cast(ReviewJobRepository, repository),
    )

    summary = await service.cleanup_expired_sandboxes()

    assert not sandbox_path.exists()
    assert repository.cleared_job_ids == [job_id]
    assert summary.scanned_jobs == 1
    assert summary.cleaned_jobs == 1
    assert summary.skipped_jobs == 0
    assert summary.freed_bytes == 5
    assert repository.cutoff is not None


@pytest.mark.asyncio
async def test_cleanup_expired_sandboxes_skips_unsafe_path(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    unsafe_path = tmp_path / "outside"
    unsafe_path.mkdir()
    job_id = uuid4()
    repository = FakeReviewJobRepository([_build_review_job(job_id, unsafe_path)])
    service = SandboxCleanupService(
        settings=Settings(sandbox_root=str(sandbox_root), sandbox_ttl_hours=1),
        review_job_repository=cast(ReviewJobRepository, repository),
    )

    summary = await service.cleanup_expired_sandboxes()

    assert unsafe_path.exists()
    assert repository.cleared_job_ids == []
    assert summary.scanned_jobs == 1
    assert summary.cleaned_jobs == 0
    assert summary.skipped_jobs == 1
    assert summary.freed_bytes == 0


def _build_review_job(job_id: UUID, sandbox_path: Path) -> ReviewJob:
    return ReviewJob(
        id=job_id,
        repository_id=uuid4(),
        user_id=uuid4(),
        status=ReviewJobStatus.COMPLETED,
        sandbox_path=str(sandbox_path),
        completed_at=datetime.now(UTC) - timedelta(hours=2),
    )
