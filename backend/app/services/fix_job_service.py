"""Fix job creation, lookup, approval, and authorization workflows."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import FixJob
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.user import User
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.fix_job import (
    FixDiffResponse,
    FixJobCreate,
    FixJobProgressEvent,
    FixJobResponse,
)
from app.services.fix_job_mapper import build_fix_job_response
from app.services.fix_job_queue_service import FixJobQueueService
from app.services.fix_notification_service import (
    build_fix_job_progress_event,
)
from app.services.fix_pipeline.workspace import build_fix_branch


class FixJobService:
    """Business workflows for generated code patches."""

    def __init__(
        self,
        *,
        fix_job_repository: FixJobRepository,
        review_job_repository: ReviewJobRepository,
        report_repository: ReportRepository,
        queue_service: FixJobQueueService,
        audit_log_repository: FixAuditLogRepository | None = None,
    ) -> None:
        self.fix_job_repository = fix_job_repository
        self.review_job_repository = review_job_repository
        self.report_repository = report_repository
        self.queue_service = queue_service
        self.audit_log_repository = audit_log_repository

    async def create_fix(
        self,
        review_job_id: UUID,
        payload: FixJobCreate,
        current_user: User,
    ) -> FixJobResponse:
        """Create a pending fix job for selected issues in a completed review."""

        review_job = await self.review_job_repository.get_by_id(review_job_id)
        if review_job is None:
            raise NotFoundError("Review job not found")

        self._ensure_review_job_owner(review_job.user_id, current_user)
        self._ensure_review_job_fixable(review_job)
        await self._ensure_selected_issues_exist(review_job.id, payload.issue_ids)

        fix_job_id = uuid4()
        target_branch = (
            payload.target_branch
            or review_job.branch
            or (
                review_job.repository.default_branch
                if review_job.repository is not None
                else None
            )
        )
        if target_branch is None:
            raise NotFoundError("Source repository is unavailable")
        fix_job = await self.fix_job_repository.create(
            fix_job_id=fix_job_id,
            review_job_id=review_job.id,
            user_id=current_user.id,
            issue_ids=payload.issue_ids,
            target_branch=target_branch,
            base_commit_sha=review_job.commit_sha or "",
            fix_branch=build_fix_branch(fix_job_id),
        )
        await self._append_audit_log(
            fix_job.id,
            action=FixAuditAction.FIX_GENERATED,
            user_id=current_user.id,
            message="Fix generation queued.",
            event_metadata={
                "issue_ids": [str(issue_id) for issue_id in payload.issue_ids]
            },
        )
        await self.queue_service.enqueue(fix_job.id)
        return build_fix_job_response(fix_job)

    async def list_fixes(
        self,
        review_job_id: UUID,
        current_user: User,
    ) -> list[FixJobResponse]:
        """Return fix jobs for an authorized review job."""

        review_job = await self.review_job_repository.get_by_id(review_job_id)
        if review_job is None:
            raise NotFoundError("Review job not found")

        self._ensure_review_job_owner(review_job.user_id, current_user)
        fix_jobs = await self.fix_job_repository.list_for_review_job(review_job_id)
        return [build_fix_job_response(fix_job) for fix_job in fix_jobs]

    async def get_fix(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobResponse:
        """Return one fix job after ownership authorization."""

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        return build_fix_job_response(fix_job)

    async def get_diff(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixDiffResponse:
        """Return the generated unified diff for a fix job."""

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        if fix_job.diff is None:
            raise NotFoundError("Fix diff is not ready")

        await self._append_audit_log(
            fix_job.id,
            action=FixAuditAction.DIFF_VIEWED,
            user_id=current_user.id,
            message="Fix diff viewed.",
        )
        return FixDiffResponse(
            fix_id=fix_job.id,
            diff=fix_job.diff,
            changed_files=fix_job.changed_files or [],
        )

    async def get_progress_snapshot(
        self,
        fix_job_id: UUID,
        current_user: User,
    ) -> FixJobProgressEvent:
        """Return an owner-authorized fallback event for a fix SSE connection."""

        fix_job = await self._get_authorized_fix(fix_job_id, current_user)
        timestamp = fix_job.completed_at or fix_job.started_at or fix_job.created_at
        return build_fix_job_progress_event(
            fix_job,
            timestamp=timestamp or datetime.now(UTC),
        )

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

    async def _ensure_selected_issues_exist(
        self,
        review_job_id: UUID,
        issue_ids: list[UUID],
    ) -> None:
        issues = await self.report_repository.list_issues_by_ids(
            job_id=review_job_id,
            issue_ids=issue_ids,
        )
        found_issue_ids = {issue.id for issue in issues}
        missing_issue_ids = [
            issue_id for issue_id in issue_ids if issue_id not in found_issue_ids
        ]
        if missing_issue_ids:
            raise NotFoundError("One or more selected issues were not found")

    def _ensure_review_job_owner(self, user_id: UUID, current_user: User) -> None:
        if user_id != current_user.id:
            raise AuthorizationError("Review job access is restricted to its owner")

    def _ensure_review_job_fixable(self, review_job: ReviewJob) -> None:
        if review_job.status != ReviewJobStatus.COMPLETED:
            raise ConflictError("Only completed review jobs can be fixed")

        if not review_job.commit_sha:
            raise ConflictError("Review job does not have a reviewed commit SHA")

    async def _append_audit_log(
        self,
        fix_job_id: UUID,
        *,
        action: FixAuditAction,
        user_id: UUID | None,
        message: str | None = None,
        event_metadata: dict[str, object] | None = None,
    ) -> None:
        if self.audit_log_repository is None:
            return

        await self.audit_log_repository.append(
            fix_job_id=fix_job_id,
            action=action,
            user_id=user_id,
            message=message,
            event_metadata=event_metadata,
        )
