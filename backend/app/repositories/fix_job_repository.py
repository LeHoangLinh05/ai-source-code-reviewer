"""Persistence operations for generated fix jobs."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fix_job import (
    FixJob,
    FixJobStatus,
    FixPublishStatus,
    FixValidationStatus,
)
from app.models.repository import RepositoryPlatform
from app.models.review_job import ReviewJob


class FixJobRepository:
    """Database access for fix job records without business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        """Persist a pending fix job."""

        fix_job = FixJob(
            id=fix_job_id,
            review_job_id=review_job_id,
            user_id=user_id,
            status=FixJobStatus.PENDING,
            validation_status=FixValidationStatus.NOT_RUN,
            issue_ids=[str(issue_id) for issue_id in issue_ids],
            target_branch=target_branch,
            base_commit_sha=base_commit_sha,
            fix_branch=fix_branch,
            issue_plan=[],
            issue_results=[],
        )
        self.session.add(fix_job)
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def get_by_id(self, fix_job_id: UUID) -> FixJob | None:
        """Return a fix job with its source review and repository."""

        statement = self._base_statement().where(FixJob.id == fix_job_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, fix_job_id: UUID) -> FixJob | None:
        """Lock one fix job while an approval transition is validated."""

        statement = select(FixJob).where(FixJob.id == fix_job_id).with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_review_job(self, review_job_id: UUID) -> list[FixJob]:
        """Return fix jobs for one review job, newest first."""

        statement = (
            self._base_statement()
            .where(FixJob.review_job_id == review_job_id)
            .order_by(FixJob.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_publish_status(
        self,
        fix_job_id: UUID,
    ) -> FixPublishStatus | None:
        """Return the latest publish status for cancellation checks."""

        statement = select(FixJob.publish_status).where(FixJob.id == fix_job_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_started(
        self,
        fix_job: FixJob,
        *,
        sandbox_path: str,
    ) -> FixJob:
        """Store worker start metadata for a fix job."""

        fix_job.status = FixJobStatus.PREPARING
        fix_job.started_at = datetime.now(UTC)
        fix_job.sandbox_path = sandbox_path
        fix_job.error_message = None
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def update_status(
        self,
        fix_job: FixJob,
        *,
        status: FixJobStatus,
    ) -> FixJob:
        """Persist a fix job status change."""

        fix_job.status = status
        if status in {FixJobStatus.APPROVED, FixJobStatus.FAILED}:
            fix_job.completed_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_requested(
        self,
        fix_job: FixJob,
        *,
        strategy: str,
        allow_failed_validation: bool,
        override_reason: str | None,
    ) -> FixJob:
        """Store user approval and move publish workflow to queued/running."""

        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_error = None
        fix_job.publish_strategy = strategy
        fix_job.publish_allow_failed_validation = allow_failed_validation
        fix_job.publish_override_reason = override_reason
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def stage_publish_requested(
        self,
        fix_job: FixJob,
        *,
        strategy: str,
        allow_failed_validation: bool,
        override_reason: str | None,
    ) -> None:
        """Stage an approval transition for a shared transaction."""

        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_error = None
        fix_job.publish_strategy = strategy
        fix_job.publish_allow_failed_validation = allow_failed_validation
        fix_job.publish_override_reason = override_reason
        await self.session.flush()

    async def stage_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> None:
        """Stage a publish failure for a shared transaction."""

        fix_job.publish_status = FixPublishStatus.FAILED
        fix_job.publish_error = error_message
        fix_job.publish_completed_at = datetime.now(UTC)
        await self.session.flush()

    async def commit_staged(self, fix_job: FixJob) -> FixJob:
        """Commit a staged job transition and refresh its state."""

        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_started(
        self,
        fix_job: FixJob,
        *,
        sandbox_path: str,
    ) -> FixJob:
        """Store publish worker start metadata."""

        fix_job.publish_status = FixPublishStatus.PUBLISHING
        fix_job.publish_error = None
        fix_job.publish_started_at = datetime.now(UTC)
        fix_job.publish_completed_at = None
        fix_job.publish_sandbox_path = sandbox_path
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_succeeded(
        self,
        fix_job: FixJob,
        *,
        provider: RepositoryPlatform,
        pr_url: str,
        published_branch: str,
        published_commit_sha: str,
        fork_repository_full_name: str | None,
        fork_branch: str | None,
        upstream_repository_full_name: str | None,
    ) -> FixJob:
        """Persist a successful branch push and pull request publish."""

        completed_at = datetime.now(UTC)
        fix_job.status = FixJobStatus.APPROVED
        fix_job.publish_status = FixPublishStatus.PUBLISHED
        fix_job.publish_error = None
        fix_job.provider = provider
        fix_job.pr_url = pr_url
        fix_job.published_branch = published_branch
        fix_job.published_commit_sha = published_commit_sha
        fix_job.fork_repository_full_name = fork_repository_full_name
        fix_job.fork_branch = fork_branch
        fix_job.upstream_repository_full_name = upstream_repository_full_name
        fix_job.publish_completed_at = completed_at
        fix_job.completed_at = completed_at
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_failed(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        """Persist a recoverable publish failure."""

        fix_job.publish_status = FixPublishStatus.FAILED
        fix_job.publish_error = error_message
        fix_job.publish_completed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_needs_fork(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        """Persist that origin publish needs a fork fallback."""

        fix_job.publish_status = FixPublishStatus.NEEDS_FORK
        fix_job.publish_error = error_message
        fix_job.publish_completed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_publish_stale_base(
        self,
        fix_job: FixJob,
        *,
        error_message: str,
    ) -> FixJob:
        """Persist that the remote base branch moved since review."""

        fix_job.publish_status = FixPublishStatus.STALE_BASE
        fix_job.publish_error = error_message
        fix_job.publish_completed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def save_patch(
        self,
        fix_job: FixJob,
        *,
        diff: str,
        changed_files: list[str],
    ) -> FixJob:
        """Persist a generated patch diff."""

        fix_job.diff = diff
        fix_job.changed_files = changed_files
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def save_issue_plan(
        self,
        fix_job: FixJob,
        *,
        issue_plan: list[dict[str, object]],
    ) -> FixJob:
        """Persist the cross-file plan for selected issues."""

        fix_job.issue_plan = issue_plan
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def save_issue_results(
        self,
        fix_job: FixJob,
        *,
        issue_results: list[dict[str, object]],
    ) -> FixJob:
        """Persist one verification result per selected issue."""

        fix_job.issue_results = issue_results
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def save_validation(
        self,
        fix_job: FixJob,
        *,
        validation_status: FixValidationStatus,
        validation_output: dict[str, object],
    ) -> FixJob:
        """Persist validation results for a generated patch."""

        fix_job.validation_status = validation_status
        fix_job.validation_output = validation_output
        await self.session.commit()
        await self.session.refresh(fix_job)
        return fix_job

    async def mark_failed_by_id(
        self,
        fix_job_id: UUID,
        *,
        error_message: str,
    ) -> None:
        """Persist a terminal failed state when the ORM object may be expired."""

        await self.session.execute(
            update(FixJob)
            .where(FixJob.id == fix_job_id)
            .values(
                status=FixJobStatus.FAILED,
                error_message=error_message,
                completed_at=datetime.now(UTC),
            )
        )
        await self.session.commit()

    async def list_expired_with_sandbox(self, cutoff: datetime) -> list[FixJob]:
        """Return terminal fix jobs with sandbox paths older than cutoff."""

        terminal_statuses = (FixJobStatus.APPROVED, FixJobStatus.FAILED)
        statement = (
            select(FixJob)
            .where(FixJob.status.in_(terminal_statuses))
            .where(FixJob.sandbox_path.is_not(None))
            .where(
                or_(
                    FixJob.completed_at <= cutoff,
                    and_(
                        FixJob.completed_at.is_(None),
                        FixJob.created_at <= cutoff,
                    ),
                )
            )
            .order_by(FixJob.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def list_expired_with_publish_sandbox(
        self,
        cutoff: datetime,
    ) -> list[FixJob]:
        """Return finished publish attempts with publish sandboxes to clean."""

        terminal_publish_statuses = (
            FixPublishStatus.PUBLISHED,
            FixPublishStatus.FAILED,
            FixPublishStatus.NEEDS_FORK,
            FixPublishStatus.STALE_BASE,
        )
        statement = (
            select(FixJob)
            .where(FixJob.publish_status.in_(terminal_publish_statuses))
            .where(FixJob.publish_sandbox_path.is_not(None))
            .where(
                or_(
                    FixJob.publish_completed_at <= cutoff,
                    and_(
                        FixJob.publish_completed_at.is_(None),
                        FixJob.created_at <= cutoff,
                    ),
                )
            )
            .order_by(FixJob.created_at.asc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def clear_sandbox_path(self, fix_job_id: UUID) -> None:
        """Mark a fix job sandbox as cleaned."""

        await self.session.execute(
            update(FixJob).where(FixJob.id == fix_job_id).values(sandbox_path=None)
        )
        await self.session.commit()

    async def clear_publish_sandbox_path(self, fix_job_id: UUID) -> None:
        """Mark a publish sandbox as cleaned."""

        await self.session.execute(
            update(FixJob)
            .where(FixJob.id == fix_job_id)
            .values(publish_sandbox_path=None)
        )
        await self.session.commit()

    async def rollback(self) -> None:
        """Discard staged fix job changes after an error."""

        await self.session.rollback()

    def _base_statement(self) -> Select[tuple[FixJob]]:
        return select(FixJob).options(
            selectinload(FixJob.review_job).selectinload(ReviewJob.repository),
            selectinload(FixJob.user),
        )
