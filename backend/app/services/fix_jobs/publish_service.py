"""Validate and initiate fix publishing requests."""

from uuid import UUID

from pydantic import ValidationError

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import (
    FixJob,
    FixJobStatus,
    FixPublishStatus,
    FixValidationStatus,
)
from app.models.user import User
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.schemas.fix_job import (
    FixAuditLogResponse,
    FixIssueResult,
    FixIssueVerdict,
    FixJobResponse,
    PublishFixPayload,
)
from app.services.fix_jobs.mapper import build_fix_job_response
from app.services.fix_jobs.notifications import publish_fix_job_progress
from app.services.fix_jobs.publish_queue import FixPublishQueueService

PUBLISH_CANCELED_MESSAGE = "Publish canceled by user."
STALE_BASE_REGENERATE_MESSAGE = (
    "Base branch changed after review; regenerate the fix before publishing."
)
VALIDATION_FAILED_BLOCK_MESSAGE = (
    "Validation failed. Re-submit with allow_failed_validation=true to publish anyway."
)
OVERRIDE_REASON_REQUIRED_MESSAGE = (
    "An override reason is required to publish failed validation."
)


class FixPublishService:
    """User-approved publish, retry, cancel, and audit workflows."""

    def __init__(
        self,
        *,
        fix_job_repository: FixJobRepository,
        audit_log_repository: FixAuditLogRepository,
        queue_service: FixPublishQueueService,
    ) -> None:
        self.fix_job_repository = fix_job_repository
        self.audit_log_repository = audit_log_repository
        self.queue_service = queue_service

    async def publish_fix(
        self,
        fix_job_id: UUID,
        payload: PublishFixPayload,
        current_user: User,
    ) -> FixJobResponse:
        """Approve a generated patch and enqueue PR publishing."""

        fix_job = await self._get_authorized_fix(
            fix_job_id,
            current_user,
            for_update=True,
        )
        if self._is_publish_idempotent(fix_job):
            return build_fix_job_response(fix_job)

        self._ensure_publishable(fix_job, payload)
        updated_job = await self._persist_publish_request(
            fix_job,
            action=FixAuditAction.PUBLISH_APPROVED,
            message="User approved publishing this fix as a pull request.",
            payload=payload,
            current_user=current_user,
        )
        try:
            await self.queue_service.enqueue(
                fix_job_id=updated_job.id,
                strategy=payload.strategy,
                allow_failed_validation=payload.allow_failed_validation,
            )
        except ServiceUnavailableError as error:
            failed_job = await self._persist_publish_failure(
                updated_job,
                error_message=str(error),
                current_user=current_user,
                action=FixAuditAction.PUBLISH_FAILED,
            )
            await publish_fix_job_progress(failed_job, message=str(error))
            raise
        await publish_fix_job_progress(
            updated_job,
            message="Publishing pull request.",
        )
        return build_fix_job_response(updated_job)

    async def retry_publish(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobResponse:
        """Retry a failed fork publish."""

        fix_job = await self._get_authorized_fix(
            fix_job_id,
            current_user,
            for_update=True,
        )
        if self._is_publish_idempotent(fix_job):
            return build_fix_job_response(fix_job)
        if fix_job.publish_status != FixPublishStatus.FAILED:
            raise ConflictError("Only failed publishes can be retried")
        if (
            fix_job.publish_allow_failed_validation
            and not fix_job.publish_override_reason
        ):
            raise ConflictError(OVERRIDE_REASON_REQUIRED_MESSAGE)

        payload = PublishFixPayload(
            allow_failed_validation=fix_job.publish_allow_failed_validation,
            override_reason=fix_job.publish_override_reason,
        )
        self._ensure_publishable(fix_job, payload)
        updated_job = await self._persist_publish_request(
            fix_job,
            action=FixAuditAction.PUBLISH_RETRIED,
            message="User retried publishing this fix.",
            payload=payload,
            current_user=current_user,
        )
        try:
            await self.queue_service.enqueue(
                fix_job_id=updated_job.id,
                strategy=payload.strategy,
                allow_failed_validation=payload.allow_failed_validation,
            )
        except ServiceUnavailableError as error:
            failed_job = await self._persist_publish_failure(
                updated_job,
                error_message=str(error),
                current_user=current_user,
                action=FixAuditAction.PUBLISH_FAILED,
            )
            await publish_fix_job_progress(failed_job, message=str(error))
            raise
        await publish_fix_job_progress(
            updated_job,
            message="Retrying pull request publishing.",
        )
        return build_fix_job_response(updated_job)

    async def cancel_publish(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobResponse:
        """Request cancellation for an in-flight publish job."""

        fix_job = await self._get_authorized_fix(
            fix_job_id,
            current_user,
            for_update=True,
        )
        if fix_job.publish_status != FixPublishStatus.PUBLISHING:
            raise ConflictError("Only an in-progress publish can be canceled")

        updated_job = await self._persist_publish_failure(
            fix_job,
            error_message=PUBLISH_CANCELED_MESSAGE,
            current_user=current_user,
            action=FixAuditAction.PUBLISH_CANCELED,
        )
        await publish_fix_job_progress(
            updated_job,
            message=PUBLISH_CANCELED_MESSAGE,
        )
        return build_fix_job_response(updated_job)

    async def list_audit_logs(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> list[FixAuditLogResponse]:
        """Return audit timeline for an authorized fix job."""

        await self._get_authorized_fix(fix_job_id, current_user)
        audit_logs = await self.audit_log_repository.list_for_fix_job(fix_job_id)
        return [
            FixAuditLogResponse.model_validate(audit_log) for audit_log in audit_logs
        ]

    async def _get_authorized_fix(
        self,
        fix_job_id: UUID,
        current_user: User,
        *,
        for_update: bool = False,
    ) -> FixJob:
        fix_job = (
            await self.fix_job_repository.get_by_id_for_update(fix_job_id)
            if for_update
            else await self.fix_job_repository.get_by_id(fix_job_id)
        )
        if fix_job is None:
            raise NotFoundError("Fix job not found")

        if fix_job.user_id != current_user.id:
            raise AuthorizationError("Fix job access is restricted to its owner")

        return fix_job

    async def _persist_publish_request(
        self,
        fix_job: FixJob,
        *,
        action: FixAuditAction,
        message: str,
        payload: PublishFixPayload,
        current_user: User,
    ) -> FixJob:
        try:
            await self.fix_job_repository.stage_publish_requested(
                fix_job,
                strategy=payload.strategy,
                allow_failed_validation=payload.allow_failed_validation,
                override_reason=payload.override_reason,
            )
            await self.audit_log_repository.stage_append(
                fix_job_id=fix_job.id,
                user_id=current_user.id,
                action=action,
                message=message,
                event_metadata={
                    "strategy": payload.strategy,
                    "allow_failed_validation": payload.allow_failed_validation,
                    "override_reason": payload.override_reason,
                    "unresolved_issue_ids": _unresolved_issue_ids(fix_job),
                    "validation_output": fix_job.validation_output,
                },
            )
            return await self.fix_job_repository.commit_staged(fix_job)
        except Exception:
            await self.fix_job_repository.rollback()
            raise

    async def _persist_publish_failure(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
        current_user: User,
        action: FixAuditAction,
    ) -> FixJob:
        try:
            await self.fix_job_repository.stage_publish_failed(
                fix_job,
                error_message=error_message,
            )
            await self.audit_log_repository.stage_append(
                fix_job_id=fix_job.id,
                user_id=current_user.id,
                action=action,
                message=error_message,
            )
            return await self.fix_job_repository.commit_staged(fix_job)
        except Exception:
            await self.fix_job_repository.rollback()
            raise

    def _ensure_publishable(
        self,
        fix_job: FixJob,
        payload: PublishFixPayload,
    ) -> None:
        if fix_job.status != FixJobStatus.WAITING_APPROVAL:
            raise ConflictError("Fix job is not waiting for approval")
        if fix_job.publish_status == FixPublishStatus.STALE_BASE:
            raise ConflictError(STALE_BASE_REGENERATE_MESSAGE)
        if not fix_job.diff or not fix_job.changed_files:
            raise ConflictError("Fix job does not have a generated diff")
        if fix_job.validation_output is None:
            raise ConflictError("Fix job does not have validation results")
        if (
            payload.allow_failed_validation
            and fix_job.validation_status != FixValidationStatus.FAILED
        ):
            raise ConflictError(
                "Validation override is only allowed when validation failed"
            )
        if (
            fix_job.validation_status == FixValidationStatus.FAILED
            and not payload.allow_failed_validation
        ):
            raise ConflictError(VALIDATION_FAILED_BLOCK_MESSAGE)
        if payload.allow_failed_validation and not payload.override_reason:
            raise ConflictError(OVERRIDE_REASON_REQUIRED_MESSAGE)

    def _is_publish_idempotent(self, fix_job: FixJob) -> bool:
        return bool(fix_job.pr_url) or fix_job.publish_status in {
            FixPublishStatus.PUBLISHED,
            FixPublishStatus.PUBLISHING,
        }


def _unresolved_issue_ids(fix_job: FixJob) -> list[str]:
    unresolved_ids: list[str] = []
    for payload in fix_job.issue_results or []:
        try:
            result = FixIssueResult.model_validate(payload)
        except ValidationError:
            continue
        if result.verdict != FixIssueVerdict.FIXED:
            unresolved_ids.append(str(result.issue_id))
    return unresolved_ids
