"""Tests for fix job service workflows."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ConflictError, NotFoundError
from app.models.fix_job import FixJob, FixJobStatus, FixValidationStatus
from app.models.repository import Repository
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.user import User, UserRole
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.fix_job import FixJobCreate
from app.services.fix_jobs.queue import FixJobQueueService
from app.services.fix_jobs.service import FixJobService


@pytest.mark.asyncio
async def test_create_fix_job_enqueues_patch_job() -> None:
    review_job = _build_review_job()
    current_user = _build_user(review_job.user_id)
    issue_id = uuid4()
    fix_job_repository = _RecordingFixJobRepository()
    report_repository = _RecordingReportRepository([issue_id])
    queue_service = _RecordingFixQueueService()
    service = FixJobService(
        fix_job_repository=cast(FixJobRepository, fix_job_repository),
        review_job_repository=cast(
            ReviewJobRepository,
            _ReviewJobLookup(review_job),
        ),
        report_repository=cast(ReportRepository, report_repository),
        queue_service=cast(FixJobQueueService, queue_service),
    )

    response = await service.create_fix(
        review_job.id,
        FixJobCreate(issue_ids=[issue_id], target_branch="feature/fix"),
        current_user,
    )

    assert response.review_job_id == review_job.id
    assert response.status == FixJobStatus.PENDING
    assert response.validation_status == FixValidationStatus.NOT_RUN
    assert response.target_branch == "feature/fix"
    assert queue_service.enqueued_fix_job_ids == [response.id]
    assert fix_job_repository.created_fix_job is not None


@pytest.mark.asyncio
async def test_create_fix_job_rejects_incomplete_review_job() -> None:
    review_job = _build_review_job(status=ReviewJobStatus.AI_REVIEWING)
    current_user = _build_user(review_job.user_id)
    service = FixJobService(
        fix_job_repository=cast(
            FixJobRepository,
            _RecordingFixJobRepository(),
        ),
        review_job_repository=cast(
            ReviewJobRepository,
            _ReviewJobLookup(review_job),
        ),
        report_repository=cast(ReportRepository, _RecordingReportRepository([])),
        queue_service=cast(FixJobQueueService, _RecordingFixQueueService()),
    )

    with pytest.raises(ConflictError, match="Only completed review jobs"):
        await service.create_fix(
            review_job.id,
            FixJobCreate(issue_ids=[uuid4()]),
            current_user,
        )


@pytest.mark.asyncio
async def test_get_progress_snapshot_returns_owner_scoped_fix_event() -> None:
    fix_job = _build_fix_job(status=FixJobStatus.VALIDATING)
    service = FixJobService(
        fix_job_repository=cast(
            FixJobRepository,
            _RecordingFixJobRepository(fix_job=fix_job),
        ),
        review_job_repository=cast(
            ReviewJobRepository,
            _ReviewJobLookup(_build_review_job()),
        ),
        report_repository=cast(ReportRepository, _RecordingReportRepository([])),
        queue_service=cast(FixJobQueueService, _RecordingFixQueueService()),
    )

    event = await service.get_progress_snapshot(
        fix_job.id,
        _build_user(fix_job.user_id),
    )

    assert event.fix_id == fix_job.id
    assert event.status == FixJobStatus.VALIDATING
    assert event.progress == 80
    assert event.data["changed_files"] == ["src/app.py"]


@pytest.mark.asyncio
async def test_get_diff_rejects_missing_diff() -> None:
    fix_job = _build_fix_job(diff=None)
    service = FixJobService(
        fix_job_repository=cast(
            FixJobRepository,
            _RecordingFixJobRepository(fix_job=fix_job),
        ),
        review_job_repository=cast(
            ReviewJobRepository,
            _ReviewJobLookup(_build_review_job()),
        ),
        report_repository=cast(ReportRepository, _RecordingReportRepository([])),
        queue_service=cast(FixJobQueueService, _RecordingFixQueueService()),
    )

    with pytest.raises(NotFoundError, match="Fix diff is not ready"):
        await service.get_diff(fix_job.id, _build_user(fix_job.user_id))


def _build_user(user_id: UUID) -> User:
    return User(
        id=user_id,
        email="user@example.com",
        hashed_password="hashed",
        full_name=None,
        is_active=True,
        role=UserRole.USER,
    )


def _build_review_job(
    *,
    status: ReviewJobStatus = ReviewJobStatus.COMPLETED,
) -> ReviewJob:
    user_id = uuid4()
    return ReviewJob(
        id=uuid4(),
        repository_id=uuid4(),
        user_id=user_id,
        status=status,
        branch="main",
        commit_sha="a" * 40,
        repository=Repository(
            id=uuid4(),
            user_id=user_id,
            name="repo",
            url="https://github.com/example/repo.git",
            default_branch="main",
        ),
    )


def _build_fix_job(
    *,
    status: FixJobStatus = FixJobStatus.WAITING_APPROVAL,
    validation_status: FixValidationStatus = FixValidationStatus.PASSED,
    diff: str | None = "diff --git a/src/app.py b/src/app.py",
    changed_files: list[str] | None = None,
    validation_output: dict[str, object] | None = None,
) -> FixJob:
    user_id = uuid4()
    review_job_id = uuid4()
    return FixJob(
        id=uuid4(),
        review_job_id=review_job_id,
        user_id=user_id,
        status=status,
        validation_status=validation_status,
        issue_ids=[str(uuid4())],
        target_branch="main",
        base_commit_sha="a" * 40,
        fix_branch=f"repoguard/fix/{uuid4()}",
        diff=diff,
        changed_files=changed_files or ["src/app.py"],
        validation_output=validation_output or _build_validation_output(),
        pr_url=None,
        started_at=None,
        completed_at=None,
        created_at=datetime.now(UTC),
    )


class _RecordingFixJobRepository:
    def __init__(self, *, fix_job: FixJob | None = None) -> None:
        self.fix_job = fix_job
        self.created_fix_job: FixJob | None = None
        self.updated_statuses: list[FixJobStatus] = []

    async def create(
        self,
        *,
        fix_job_id: UUID,
        review_job_id: UUID,
        user_id: UUID,
        issue_ids: list[UUID],
        target_branch: str,
        base_commit_sha: str,
        fix_branch: str,
    ) -> FixJob:
        self.created_fix_job = FixJob(
            id=fix_job_id,
            review_job_id=review_job_id,
            user_id=user_id,
            status=FixJobStatus.PENDING,
            validation_status=FixValidationStatus.NOT_RUN,
            issue_ids=[str(issue_id) for issue_id in issue_ids],
            target_branch=target_branch,
            base_commit_sha=base_commit_sha,
            fix_branch=fix_branch,
            changed_files=None,
            validation_output=None,
            pr_url=None,
            started_at=None,
            completed_at=None,
            created_at=datetime.now(UTC),
        )
        return self.created_fix_job

    async def get_by_id(self, _fix_job_id: UUID) -> FixJob | None:
        return self.fix_job or self.created_fix_job

    async def list_for_review_job(self, _review_job_id: UUID) -> list[FixJob]:
        return [self.fix_job] if self.fix_job is not None else []

    async def update_status(self, fix_job: FixJob, *, status: FixJobStatus) -> FixJob:
        self.updated_statuses.append(status)
        fix_job.status = status
        return fix_job

    async def save_patch(
        self,
        fix_job: FixJob,
        *,
        diff: str,
        changed_files: list[str],
    ) -> FixJob:
        fix_job.diff = diff
        fix_job.changed_files = changed_files
        return fix_job

    async def save_validation(
        self,
        fix_job: FixJob,
        *,
        validation_status: FixValidationStatus,
        validation_output: dict[str, object],
    ) -> FixJob:
        fix_job.validation_status = validation_status
        fix_job.validation_output = validation_output
        return fix_job

    async def mark_failed_by_id(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _RecordingFixQueueService:
    def __init__(self) -> None:
        self.enqueued_fix_job_ids: list[UUID] = []

    async def enqueue(self, fix_job_id: UUID) -> None:
        self.enqueued_fix_job_ids.append(fix_job_id)


class _ReviewJobLookup:
    def __init__(self, review_job: ReviewJob) -> None:
        self.review_job = review_job

    async def get_by_id(self, _review_job_id: UUID) -> ReviewJob | None:
        return self.review_job


class _RecordingReportRepository:
    def __init__(self, issue_ids: list[UUID]) -> None:
        self.issue_ids = issue_ids

    async def list_issues_by_ids(
        self,
        *,
        job_id: UUID,
        issue_ids: list[UUID],
    ) -> list[SimpleNamespace]:
        _ = job_id
        return [
            SimpleNamespace(id=issue_id)
            for issue_id in issue_ids
            if issue_id in self.issue_ids
        ]


def _build_validation_output() -> dict[str, object]:
    return {
        "status": FixValidationStatus.PASSED.value,
        "summary": "Validation passed: 1 passed, 0 failed, 0 skipped.",
        "checks": [
            {
                "name": "ruff check",
                "command": "ruff check src/app.py",
                "status": "passed",
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_ms": 12,
            }
        ],
    }
