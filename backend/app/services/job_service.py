"""Review job creation, status lookup, cancellation, and queue workflows."""

from datetime import UTC, datetime
import logging
from uuid import UUID

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.review_job import ReviewJob, ReviewJobStatus
from app.models.user import User, UserRole
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.schemas.review_job import (
    ReviewJobCreate,
    ReviewJobCreateResponse,
    ReviewJobResponse,
    ReviewJobStatusUpdate,
)
from app.services.job_queue_service import JobQueueService
from app.services.notification_service import publish_job_progress

logger = logging.getLogger(__name__)


class ReviewJobService:
    """Business workflows for review job lifecycle management."""

    def __init__(
        self,
        review_job_repository: ReviewJobRepository,
        repository_repository: RepositoryRepository,
        job_queue_service: JobQueueService,
    ) -> None:
        self.review_job_repository = review_job_repository
        self.repository_repository = repository_repository
        self.job_queue_service = job_queue_service

    async def create_job(
        self,
        payload: ReviewJobCreate,
        current_user: User,
    ) -> ReviewJobCreateResponse:
        """Create a pending review job and enqueue real worker processing."""

        source_repository = await self.repository_repository.get_by_id(
            payload.repository_id
        )
        if source_repository is None:
            raise NotFoundError("Repository not found")

        if (
            current_user.role != UserRole.ADMIN
            and source_repository.user_id != current_user.id
        ):
            raise AuthorizationError("Repository access is restricted to its owner")

        branch = payload.branch or source_repository.default_branch
        try:
            review_job = await self.review_job_repository.create(
                repository_id=source_repository.id,
                user_id=current_user.id,
                branch=branch,
                options=payload.options,
            )
        except Exception:
            await self.review_job_repository.rollback()
            raise

        await self.job_queue_service.enqueue(review_job.id)
        return ReviewJobCreateResponse(
            job_id=review_job.id,
            status=review_job.status,
            created_at=review_job.created_at,
            stream_url=self._build_stream_url(review_job.id),
        )

    async def list_jobs(
        self,
        current_user: User,
        *,
        status: ReviewJobStatus | None,
        repository_id: UUID | None,
    ) -> list[ReviewJobResponse]:
        """List review jobs visible to the current user."""

        if current_user.role == UserRole.ADMIN:
            review_jobs = await self.review_job_repository.list_all(
                status=status,
                repository_id=repository_id,
            )
        else:
            review_jobs = await self.review_job_repository.list_for_user(
                current_user.id,
                status=status,
                repository_id=repository_id,
            )

        return [self._to_response(review_job) for review_job in review_jobs]

    async def get_job(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReviewJobResponse:
        """Return a review job after ownership or admin authorization."""

        review_job = await self._get_authorized_job(job_id, current_user)
        return self._to_response(review_job)

    async def cancel_job(self, job_id: UUID, current_user: User) -> None:
        """Cancel a review job by deleting it while it is still active."""

        review_job = await self._get_authorized_job(job_id, current_user)
        if review_job.status in {ReviewJobStatus.COMPLETED, ReviewJobStatus.FAILED}:
            raise ConflictError("Completed or failed review jobs cannot be canceled")

        await self.job_queue_service.cancel(job_id)
        try:
            await self.review_job_repository.delete(review_job)
        except Exception:
            await self.review_job_repository.rollback()
            raise

        logger.info("Review job %s canceled by user %s", job_id, current_user.id)

    async def update_job_status(
        self,
        job_id: UUID,
        payload: ReviewJobStatusUpdate,
        current_user: User,
    ) -> ReviewJobResponse:
        """Dev helper for manually adjusting job status in local UI tests."""

        review_job = await self._get_authorized_job(job_id, current_user)
        try:
            updated_job = await self.review_job_repository.update_status(
                review_job,
                status=payload.status,
                message=payload.message or f"Status changed to {payload.status.value}",
                progress=payload.progress,
            )
            if payload.status == ReviewJobStatus.COMPLETED:
                completed_at = updated_job.completed_at or datetime.now(UTC)
                source_repository = await self.repository_repository.get_by_id(
                    updated_job.repository_id
                )
                if source_repository is None:
                    raise NotFoundError("Repository not found")

                await self.repository_repository.update_last_reviewed_at(
                    source_repository,
                    completed_at,
                )
        except Exception:
            await self.review_job_repository.rollback()
            await self.repository_repository.rollback()
            raise

        logger.info(
            "Review job %s status simulated as %s by user %s",
            job_id,
            payload.status.value,
            current_user.id,
        )
        await publish_job_progress(
            job_id,
            self._get_progress_event_type(payload.status),
            {
                "status": payload.status.value,
                "progress": payload.progress,
                "message": payload.message
                or f"Status changed to {payload.status.value}",
            },
        )
        return self._to_response(updated_job)

    async def _get_authorized_job(
        self,
        job_id: UUID,
        current_user: User,
    ) -> ReviewJob:
        review_job = await self.review_job_repository.get_by_id(job_id)
        if review_job is None:
            raise NotFoundError("Review job not found")

        if current_user.role == UserRole.ADMIN:
            return review_job

        if review_job.user_id != current_user.id:
            raise AuthorizationError("Review job access is restricted to its owner")

        return review_job

    def _to_response(self, review_job: ReviewJob) -> ReviewJobResponse:
        return ReviewJobResponse(
            id=review_job.id,
            repository_id=review_job.repository_id,
            repository_name=review_job.repository.name
            if review_job.repository is not None
            else None,
            user_id=review_job.user_id,
            status=review_job.status,
            branch=review_job.branch,
            commit_sha=review_job.commit_sha,
            error_message=review_job.error_message,
            options=review_job.options,
            started_at=review_job.started_at,
            completed_at=review_job.completed_at,
            created_at=review_job.created_at,
            stream_url=self._build_stream_url(review_job.id),
        )

    def _build_stream_url(self, job_id: UUID) -> str:
        return f"/api/review-jobs/{job_id}/stream"

    def _get_progress_event_type(self, status: ReviewJobStatus) -> str:
        if status == ReviewJobStatus.COMPLETED:
            return "completed"

        if status == ReviewJobStatus.FAILED:
            return "failed"

        return "status_change"
