"""Persistence operations for review jobs and status history."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
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

    async def list_all(
        self,
        *,
        status: ReviewJobStatus | None,
        repository_id: UUID | None,
    ) -> list[ReviewJob]:
        """Return all review jobs, newest first."""

        statement = (
            select(ReviewJob)
            .options(selectinload(ReviewJob.repository))
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
