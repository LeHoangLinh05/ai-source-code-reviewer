"""Tests for user-approved fix publish workflows."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import AuthorizationError, ConflictError
from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import (
    FixJob,
    FixJobStatus,
    FixPublishStatus,
    FixValidationStatus,
)
from app.models.user import User, UserRole
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.schemas.fix_job import PublishFixPayload
from app.services import fix_publish_service as fix_publish_service_module
from app.services.fix_publish_queue_service import FixPublishQueueService
from app.services.fix_publish_service import FixPublishService


@pytest.mark.asyncio
async def test_publish_fix_enqueues_pull_request_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job()
    repository = RecordingFixJobRepository(fix_job)
    queue_service = RecordingPublishQueueService()
    service = build_service(repository, queue_service=queue_service)
    monkeypatch.setattr(
        fix_publish_service_module,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    response = await service.publish_fix(
        fix_job.id,
        PublishFixPayload(allow_failed_validation=False),
        build_user(fix_job.user_id),
    )

    assert response.publish_status == FixPublishStatus.PUBLISHING
    assert queue_service.enqueued == [(fix_job.id, "fork", False)]
    assert repository.publish_requests == [("fork", False)]


@pytest.mark.asyncio
async def test_publish_fix_rejects_non_owner() -> None:
    fix_job = build_fix_job()
    service = build_service(RecordingFixJobRepository(fix_job))

    with pytest.raises(AuthorizationError):
        await service.publish_fix(
            fix_job.id,
            PublishFixPayload(),
            build_user(uuid4()),
        )


@pytest.mark.asyncio
async def test_publish_fix_rejects_unready_fix() -> None:
    fix_job = build_fix_job(
        diff=None,
        changed_files=None,
        validation_output=None,
    )
    service = build_service(RecordingFixJobRepository(fix_job))

    with pytest.raises(ConflictError, match="generated diff"):
        await service.publish_fix(
            fix_job.id,
            PublishFixPayload(),
            build_user(fix_job.user_id),
        )


@pytest.mark.asyncio
async def test_publish_fix_blocks_failed_validation_without_override() -> None:
    fix_job = build_fix_job(validation_status=FixValidationStatus.FAILED)
    service = build_service(RecordingFixJobRepository(fix_job))

    with pytest.raises(ConflictError, match="Validation failed"):
        await service.publish_fix(
            fix_job.id,
            PublishFixPayload(allow_failed_validation=False),
            build_user(fix_job.user_id),
        )


@pytest.mark.asyncio
async def test_publish_fix_is_idempotent_after_pr_url_exists() -> None:
    fix_job = build_fix_job(
        publish_status=FixPublishStatus.PUBLISHED,
        pr_url="https://github.com/example/repo/pull/1",
    )
    queue_service = RecordingPublishQueueService()
    service = build_service(
        RecordingFixJobRepository(fix_job),
        queue_service=queue_service,
    )

    response = await service.publish_fix(
        fix_job.id,
        PublishFixPayload(),
        build_user(fix_job.user_id),
    )

    assert response.pr_url == fix_job.pr_url
    assert queue_service.enqueued == []


@pytest.mark.asyncio
async def test_retry_publish_requeues_failed_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job(
        publish_status=FixPublishStatus.FAILED,
        publish_strategy="fork",
        publish_allow_failed_validation=True,
        validation_status=FixValidationStatus.FAILED,
    )
    repository = RecordingFixJobRepository(fix_job)
    queue_service = RecordingPublishQueueService()
    service = build_service(repository, queue_service=queue_service)
    monkeypatch.setattr(
        fix_publish_service_module,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    response = await service.retry_publish(fix_job.id, build_user(fix_job.user_id))

    assert response.publish_status == FixPublishStatus.PUBLISHING
    assert queue_service.enqueued == [(fix_job.id, "fork", True)]
    assert repository.publish_requests == [("fork", True)]


@pytest.mark.asyncio
async def test_cancel_publish_marks_publish_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_job = build_fix_job(publish_status=FixPublishStatus.PUBLISHING)
    repository = RecordingFixJobRepository(fix_job)
    audit_repository = RecordingAuditLogRepository()
    service = build_service(repository, audit_repository=audit_repository)
    monkeypatch.setattr(
        fix_publish_service_module,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    response = await service.cancel_publish(fix_job.id, build_user(fix_job.user_id))

    assert response.publish_status == FixPublishStatus.FAILED
    assert "canceled" in (response.publish_error or "")
    assert audit_repository.actions == [FixAuditAction.PUBLISH_CANCELED]


def build_service(
    repository: "RecordingFixJobRepository",
    *,
    audit_repository: "RecordingAuditLogRepository | None" = None,
    queue_service: "RecordingPublishQueueService | None" = None,
) -> FixPublishService:
    return FixPublishService(
        fix_job_repository=cast(FixJobRepository, repository),
        audit_log_repository=cast(
            FixAuditLogRepository,
            audit_repository or RecordingAuditLogRepository(),
        ),
        queue_service=cast(
            FixPublishQueueService,
            queue_service or RecordingPublishQueueService(),
        ),
    )


def build_user(user_id: UUID) -> User:
    return User(
        id=user_id,
        email="user@example.com",
        hashed_password="hashed",
        full_name=None,
        is_active=True,
        role=UserRole.USER,
    )


def build_fix_job(
    *,
    status: FixJobStatus = FixJobStatus.WAITING_APPROVAL,
    validation_status: FixValidationStatus = FixValidationStatus.PASSED,
    publish_status: FixPublishStatus = FixPublishStatus.NOT_REQUESTED,
    publish_strategy: str = "fork",
    publish_allow_failed_validation: bool = False,
    diff: str | None = "diff --git a/src/app.py b/src/app.py",
    changed_files: list[str] | None = None,
    validation_output: dict[str, object] | None = None,
    pr_url: str | None = None,
) -> FixJob:
    return FixJob(
        id=uuid4(),
        review_job_id=uuid4(),
        user_id=uuid4(),
        status=status,
        validation_status=validation_status,
        issue_ids=[str(uuid4())],
        target_branch="main",
        base_commit_sha="a" * 40,
        fix_branch=f"repoguard/fix/{uuid4()}",
        diff=diff,
        changed_files=changed_files or ["src/app.py"],
        validation_output=validation_output or build_validation_output(),
        publish_status=publish_status,
        publish_strategy=publish_strategy,
        publish_allow_failed_validation=publish_allow_failed_validation,
        publish_error=None,
        pr_url=pr_url,
        created_at=datetime.now(UTC),
    )


def build_validation_output() -> dict[str, object]:
    return {
        "status": FixValidationStatus.PASSED.value,
        "summary": "Validation passed: 1 passed, 0 failed, 0 skipped.",
        "checks": [],
    }


async def noop_publish_progress(*_args: object, **_kwargs: object) -> int:
    return 0


class RecordingFixJobRepository:
    def __init__(self, fix_job: FixJob) -> None:
        self.fix_job = fix_job
        self.publish_requests: list[tuple[str, bool]] = []

    async def get_by_id(self, _fix_job_id: UUID) -> FixJob | None:
        return self.fix_job

    async def mark_publish_requested(
        self,
        fix_job: FixJob,
        *,
        strategy: str,
        allow_failed_validation: bool,
    ) -> FixJob:
        self.publish_requests.append((strategy, allow_failed_validation))
        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_strategy = strategy
        fix_job.publish_allow_failed_validation = allow_failed_validation
        fix_job.publish_error = None
        return fix_job

    async def mark_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.FAILED
        fix_job.publish_error = error_message
        return fix_job


class RecordingAuditLogRepository:
    def __init__(self) -> None:
        self.actions: list[FixAuditAction] = []

    async def append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        _ = fix_job_id, user_id, message, event_metadata
        self.actions.append(action)

    async def list_for_fix_job(self, _fix_job_id: UUID) -> list[object]:
        return []


class RecordingPublishQueueService:
    def __init__(self) -> None:
        self.enqueued: list[tuple[UUID, str, bool]] = []

    async def enqueue(
        self,
        *,
        fix_job_id: UUID,
        strategy: str,
        allow_failed_validation: bool,
    ) -> None:
        self.enqueued.append((fix_job_id, strategy, allow_failed_validation))
