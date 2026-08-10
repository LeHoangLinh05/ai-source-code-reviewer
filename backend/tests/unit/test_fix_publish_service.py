"""Tests for user-approved fix publish workflows."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
from app.schemas.fix_job import FixIssueResult, FixIssueVerdict, PublishFixPayload
from app.services import fix_publish_service as fix_publish_service_module
from app.services.fix_publish_queue_service import FixPublishQueueService
from app.services.fix_publish_service import FixPublishService


def test_publish_override_requires_an_auditable_reason() -> None:
    with pytest.raises(ValidationError, match="override_reason"):
        PublishFixPayload(
            allow_failed_validation=True,
            override_reason="too short",
        )


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
    assert repository.publish_requests == [("fork", False, None)]


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
async def test_publish_fix_rejects_override_when_validation_passed() -> None:
    fix_job = build_fix_job(validation_status=FixValidationStatus.PASSED)
    service = build_service(RecordingFixJobRepository(fix_job))

    with pytest.raises(ConflictError, match="only allowed when validation failed"):
        await service.publish_fix(
            fix_job.id,
            PublishFixPayload(
                allow_failed_validation=True,
                override_reason="Manual override should not apply to a passing job.",
            ),
            build_user(fix_job.user_id),
        )


@pytest.mark.asyncio
async def test_publish_override_audits_unresolved_issue_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_id = uuid4()
    fix_job = build_fix_job(
        validation_status=FixValidationStatus.FAILED,
        issue_results=[
            FixIssueResult(
                issue_id=issue_id,
                probe_id="security.mass_assignment",
                verdict=FixIssueVerdict.UNRESOLVED,
                summary="Sensitive field remains",
                verification_attempts=2,
            ).model_dump(mode="json")
        ],
    )
    audit_repository = RecordingAuditLogRepository()
    service = build_service(
        RecordingFixJobRepository(fix_job),
        audit_repository=audit_repository,
    )
    monkeypatch.setattr(
        fix_publish_service_module,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    await service.publish_fix(
        fix_job.id,
        PublishFixPayload(
            allow_failed_validation=True,
            override_reason="Emergency release requires manual verification.",
        ),
        build_user(fix_job.user_id),
    )

    assert audit_repository.metadata[0]["unresolved_issue_ids"] == [str(issue_id)]
    assert audit_repository.metadata[0]["override_reason"] == (
        "Emergency release requires manual verification."
    )
    assert audit_repository.metadata[0]["validation_output"] == (
        fix_job.validation_output
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
        publish_override_reason="Retry the previously approved manual override.",
        validation_status=FixValidationStatus.FAILED,
    )
    repository = RecordingFixJobRepository(fix_job)
    queue_service = RecordingPublishQueueService()
    audit_repository = RecordingAuditLogRepository()
    service = build_service(
        repository,
        audit_repository=audit_repository,
        queue_service=queue_service,
    )
    monkeypatch.setattr(
        fix_publish_service_module,
        "publish_fix_job_progress",
        noop_publish_progress,
    )

    response = await service.retry_publish(fix_job.id, build_user(fix_job.user_id))

    assert response.publish_status == FixPublishStatus.PUBLISHING
    assert queue_service.enqueued == [(fix_job.id, "fork", True)]
    assert repository.publish_requests == [
        ("fork", True, "Retry the previously approved manual override.")
    ]
    assert audit_repository.metadata[0]["unresolved_issue_ids"] == []
    assert audit_repository.metadata[0]["override_reason"] == (
        "Retry the previously approved manual override."
    )
    assert audit_repository.metadata[0]["validation_output"] == (
        fix_job.validation_output
    )


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
    publish_override_reason: str | None = None,
    diff: str | None = "diff --git a/src/app.py b/src/app.py",
    changed_files: list[str] | None = None,
    validation_output: dict[str, object] | None = None,
    issue_results: list[dict[str, object]] | None = None,
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
        issue_plan=[],
        issue_results=issue_results or [],
        publish_status=publish_status,
        publish_strategy=publish_strategy,
        publish_allow_failed_validation=publish_allow_failed_validation,
        publish_override_reason=publish_override_reason,
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
        self.publish_requests: list[tuple[str, bool, str | None]] = []
        self.commit_count = 0
        self.rollback_count = 0

    async def get_by_id(self, _fix_job_id: UUID) -> FixJob | None:
        return self.fix_job

    async def get_by_id_for_update(self, _fix_job_id: UUID) -> FixJob | None:
        return self.fix_job

    async def mark_publish_requested(
        self,
        fix_job: FixJob,
        *,
        strategy: str,
        allow_failed_validation: bool,
        override_reason: str | None,
    ) -> FixJob:
        self.publish_requests.append(
            (strategy, allow_failed_validation, override_reason)
        )
        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_strategy = strategy
        fix_job.publish_allow_failed_validation = allow_failed_validation
        fix_job.publish_override_reason = override_reason
        fix_job.publish_error = None
        return fix_job

    async def stage_publish_requested(
        self,
        fix_job: FixJob,
        *,
        strategy: str,
        allow_failed_validation: bool,
        override_reason: str | None,
    ) -> None:
        await self.mark_publish_requested(
            fix_job,
            strategy=strategy,
            allow_failed_validation=allow_failed_validation,
            override_reason=override_reason,
        )

    async def mark_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        fix_job.publish_status = FixPublishStatus.FAILED
        fix_job.publish_error = error_message
        return fix_job

    async def stage_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> None:
        await self.mark_publish_failed(fix_job, error_message=error_message)

    async def commit_staged(self, fix_job: FixJob) -> FixJob:
        self.commit_count += 1
        return fix_job

    async def rollback(self) -> None:
        self.rollback_count += 1


class RecordingAuditLogRepository:
    def __init__(self) -> None:
        self.actions: list[FixAuditAction] = []
        self.metadata: list[dict[str, object]] = []

    async def append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        _ = fix_job_id, user_id, message
        self.actions.append(action)
        self.metadata.append(event_metadata or {})

    async def stage_append(
        self,
        *,
        fix_job_id: UUID,
        action: FixAuditAction,
        user_id: UUID | None = None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        await self.append(
            fix_job_id=fix_job_id,
            action=action,
            user_id=user_id,
            message=message,
            event_metadata=event_metadata,
        )

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
