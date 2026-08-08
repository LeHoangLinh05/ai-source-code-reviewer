"""API-facing publish workflows for generated fix jobs."""

from uuid import UUID

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
    FixJobResponse,
    PublishFixPayload,
)
from app.services.fix_job_mapper import build_fix_job_response
from app.services.fix_notification_service import publish_fix_job_progress
from app.services.fix_publish_queue_service import FixPublishQueueService

PUBLISH_CANCELED_MESSAGE = "Publish canceled by user."
STALE_BASE_REGENERATE_MESSAGE = (
    "Base branch changed after review; regenerate the fix before publishing."
)
VALIDATION_FAILED_BLOCK_MESSAGE = (
    "Validation failed. Re-submit with allow_failed_validation=true to publish anyway."
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

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        if self._is_publish_idempotent(fix_job):
            return build_fix_job_response(fix_job)

        self._ensure_publishable(fix_job, payload)
        updated_job = await self.fix_job_repository.mark_publish_requested(
            fix_job,
            strategy=payload.strategy,
            allow_failed_validation=payload.allow_failed_validation,
        )
        await self.audit_log_repository.append(
            fix_job_id=updated_job.id,
            user_id=current_user.id,
            action=FixAuditAction.PUBLISH_APPROVED,
            message="User approved publishing this fix as a pull request.",
            event_metadata={
                "strategy": payload.strategy,
                "allow_failed_validation": payload.allow_failed_validation,
            },
        )
        await publish_fix_job_progress(
            updated_job,
            message="Publishing pull request.",
        )
        try:
            await self.queue_service.enqueue(
                fix_job_id=updated_job.id,
                strategy=payload.strategy,
                allow_failed_validation=payload.allow_failed_validation,
            )
        except ServiceUnavailableError as error:
            failed_job = await self.fix_job_repository.mark_publish_failed(
                updated_job,
                error_message=str(error),
            )
            await self.audit_log_repository.append(
                fix_job_id=failed_job.id,
                user_id=current_user.id,
                action=FixAuditAction.PUBLISH_FAILED,
                message=str(error),
            )
            await publish_fix_job_progress(failed_job, message=str(error))
            raise
        return build_fix_job_response(updated_job)

    async def retry_publish(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobResponse:
        """Retry a failed fork publish."""

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        if self._is_publish_idempotent(fix_job):
            return build_fix_job_response(fix_job)
        if fix_job.publish_status != FixPublishStatus.FAILED:
            raise ConflictError("Only failed publishes can be retried")

        payload = PublishFixPayload(
            allow_failed_validation=fix_job.publish_allow_failed_validation,
        )
        self._ensure_publishable(fix_job, payload)
        updated_job = await self.fix_job_repository.mark_publish_requested(
            fix_job,
            strategy=payload.strategy,
            allow_failed_validation=payload.allow_failed_validation,
        )
        await self.audit_log_repository.append(
            fix_job_id=updated_job.id,
            user_id=current_user.id,
            action=FixAuditAction.PUBLISH_RETRIED,
            message="User retried publishing this fix.",
            event_metadata={"strategy": payload.strategy},
        )
        await publish_fix_job_progress(
            updated_job,
            message="Retrying pull request publishing.",
        )
        try:
            await self.queue_service.enqueue(
                fix_job_id=updated_job.id,
                strategy=payload.strategy,
                allow_failed_validation=payload.allow_failed_validation,
            )
        except ServiceUnavailableError as error:
            failed_job = await self.fix_job_repository.mark_publish_failed(
                updated_job,
                error_message=str(error),
            )
            await self.audit_log_repository.append(
                fix_job_id=failed_job.id,
                user_id=current_user.id,
                action=FixAuditAction.PUBLISH_FAILED,
                message=str(error),
            )
            await publish_fix_job_progress(failed_job, message=str(error))
            raise
        return build_fix_job_response(updated_job)

    async def cancel_publish(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobResponse:
        """Request cancellation for an in-flight publish job."""

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        if fix_job.publish_status != FixPublishStatus.PUBLISHING:
            raise ConflictError("Only an in-progress publish can be canceled")

        updated_job = await self.fix_job_repository.mark_publish_failed(
            fix_job,
            error_message=PUBLISH_CANCELED_MESSAGE,
        )
        await self.audit_log_repository.append(
            fix_job_id=updated_job.id,
            user_id=current_user.id,
            action=FixAuditAction.PUBLISH_CANCELED,
            message=PUBLISH_CANCELED_MESSAGE,
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
    ) -> FixJob:
        fix_job = await self.fix_job_repository.get_by_id(fix_job_id)
        if fix_job is None:
            raise NotFoundError("Fix job not found")

        if fix_job.user_id != current_user.id:
            raise AuthorizationError("Fix job access is restricted to its owner")

        return fix_job

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
            fix_job.validation_status == FixValidationStatus.FAILED
            and not payload.allow_failed_validation
        ):
            raise ConflictError(VALIDATION_FAILED_BLOCK_MESSAGE)

    def _is_publish_idempotent(self, fix_job: FixJob) -> bool:
        return bool(fix_job.pr_url) or fix_job.publish_status in {
            FixPublishStatus.PUBLISHED,
            FixPublishStatus.PUBLISHING,
        }
