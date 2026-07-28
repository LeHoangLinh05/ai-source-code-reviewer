"""Persistence operations for review jobs and status history."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job_status_history import JobStatusHistory
from app.models.review_job import ReviewJob, ReviewJobStatus


class ReviewJobRepository:
    """Database access for review job records without business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        repository_id: UUID,
        user_id: UUID,
        branch: str,
        options: dict[str, object],
    ) -> ReviewJob:
        """Persist a pending review job with its initial status history."""

        review_job = ReviewJob(
            repository_id=repository_id,
            user_id=user_id,
            status=ReviewJobStatus.PENDING,
            branch=branch,
            options=options,
        )
        self.session.add(review_job)
        await self.session.flush()
        self.session.add(
            JobStatusHistory(
                job_id=review_job.id,
                status=ReviewJobStatus.PENDING.value,
                message="Job created",
                progress=0,
            )
        )
        await self.session.commit()
        await self.session.refresh(review_job)
        return review_job

    async def get_by_id(self, job_id: UUID) -> ReviewJob | None:
        """Return a review job by primary key."""

        statement = (
            select(ReviewJob)
            .options(selectinload(ReviewJob.repository))
            .where(ReviewJob.id == job_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_latest_status_history(
        self,
        job_id: UUID,
    ) -> JobStatusHistory | None:
        """Return the newest persisted progress snapshot for a review job."""

        statement = (
            select(JobStatusHistory)
            .where(JobStatusHistory.job_id == job_id)
            .order_by(JobStatusHistory.changed_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: ReviewJobStatus | None,
        repository_id: UUID | None,
    ) -> list[ReviewJob]:
        """Return review jobs created by a user, newest first."""

        statement = (
            select(ReviewJob)
            .options(selectinload(ReviewJob.repository))
            .where(ReviewJob.user_id == user_id)
            .order_by(ReviewJob.created_at.desc())
        )
        statement = self._apply_filters(
            statement,
            status=status,
            repository_id=repository_id,
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update_status(
        self,
        review_job: ReviewJob,
        *,
        status: ReviewJobStatus,
        message: str,
        progress: int,
    ) -> ReviewJob:
        """Persist a job status change and append history."""

        review_job.status = status
        if status in {ReviewJobStatus.COMPLETED, ReviewJobStatus.FAILED}:
            review_job.completed_at = datetime.now(UTC)

        self.session.add(
            JobStatusHistory(
                job_id=review_job.id,
                status=status.value,
                message=message,
                progress=progress,
            )
        )
        await self.session.commit()
        await self.session.refresh(review_job)
        return review_job

    async def mark_started(
        self,
        review_job: ReviewJob,
        *,
        sandbox_path: str,
    ) -> ReviewJob:
        """Store worker start metadata for a job."""

        review_job.started_at = datetime.now(UTC)
        review_job.sandbox_path = sandbox_path
        review_job.error_message = None
        await self.session.commit()
        await self.session.refresh(review_job)
        return review_job

    async def update_clone_metadata(
        self,
        review_job: ReviewJob,
        *,
        commit_sha: str,
    ) -> ReviewJob:
        """Store git metadata discovered after a successful clone."""

        review_job.commit_sha = commit_sha
        await self.session.commit()
        await self.session.refresh(review_job)
        return review_job

    async def mark_failed(
        self,
        review_job: ReviewJob,
        *,
        error_message: str,
        progress: int,
    ) -> ReviewJob:
        """Persist a terminal failed state with an operator-readable error."""

        review_job.status = ReviewJobStatus.FAILED
        review_job.error_message = error_message
        review_job.completed_at = datetime.now(UTC)
        self.session.add(
            JobStatusHistory(
                job_id=review_job.id,
                status=ReviewJobStatus.FAILED.value,
                message=error_message,
                progress=progress,
            )
        )
        await self.session.commit()
        await self.session.refresh(review_job)
        return review_job

    async def mark_failed_by_id(
        self,
        job_id: UUID,
        *,
        error_message: str,
        progress: int,
    ) -> None:
        """Persist a terminal failed state when the ORM object may be expired."""

        await self.session.execute(
            update(ReviewJob)
            .where(ReviewJob.id == job_id)
            .values(
                status=ReviewJobStatus.FAILED,
                error_message=error_message,
                completed_at=datetime.now(UTC),
            )
        )
        self.session.add(
            JobStatusHistory(
                job_id=job_id,
                status=ReviewJobStatus.FAILED.value,
                message=error_message,
                progress=progress,
            )
        )
        await self.session.commit()

    async def list_expired_with_sandbox(self, cutoff: datetime) -> list[ReviewJob]:
        """Return terminal jobs with sandbox paths older than cutoff."""

        terminal_statuses = (
            ReviewJobStatus.COMPLETED,
            ReviewJobStatus.FAILED,
        )
        statement = (
            select(ReviewJob)
            .where(ReviewJob.status.in_(terminal_statuses))
            .where(ReviewJob.sandbox_path.is_not(None))
            .where(
                or_(
                    ReviewJob.completed_at <= cutoff,
                    and_(
                        ReviewJob.completed_at.is_(None),
                        ReviewJob.created_at <= cutoff,
                    ),
                )
            )
            .order_by(ReviewJob.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def clear_sandbox_path(self, job_id: UUID) -> None:
        """Mark a job sandbox as cleaned."""

        await self.session.execute(
            update(ReviewJob).where(ReviewJob.id == job_id).values(sandbox_path=None)
        )
        await self.session.commit()

    async def delete(self, review_job: ReviewJob) -> None:
        """Delete a review job and cascade its report, issues, and history."""

        await self.session.delete(review_job)
        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged review job changes after an error."""

        await self.session.rollback()

    def _apply_filters(
        self,
        statement: Select[tuple[ReviewJob]],
        *,
        status: ReviewJobStatus | None,
        repository_id: UUID | None,
    ) -> Select[tuple[ReviewJob]]:
        if status is not None:
            statement = statement.where(ReviewJob.status == status)
        if repository_id is not None:
            statement = statement.where(ReviewJob.repository_id == repository_id)
        return statement
