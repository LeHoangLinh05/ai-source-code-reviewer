"""Sandbox cleanup rules for completed review jobs."""

import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from app.core.config import Settings
from app.models.fix_job import FixJob
from app.models.review_job import ReviewJob
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.review_job_repository import ReviewJobRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SandboxCleanupSummary:
    """Aggregate result of one sandbox cleanup sweep."""

    scanned_jobs: int
    cleaned_jobs: int
    skipped_jobs: int
    freed_bytes: int


class SandboxCleanupService:
    """Remove expired job sandboxes and clear their database pointer."""

    def __init__(
        self,
        *,
        settings: Settings,
        review_job_repository: ReviewJobRepository,
        fix_job_repository: FixJobRepository | None = None,
    ) -> None:
        self.settings = settings
        self.review_job_repository = review_job_repository
        self.fix_job_repository = fix_job_repository

    async def cleanup_expired_sandboxes(self) -> SandboxCleanupSummary:
        """Clean terminal job sandboxes older than the configured TTL."""

        cutoff = datetime.now(UTC) - timedelta(hours=self.settings.sandbox_ttl_hours)
        sandbox_root = Path(self.settings.sandbox_root)
        review_jobs = await self.review_job_repository.list_expired_with_sandbox(cutoff)
        fix_jobs = (
            await self.fix_job_repository.list_expired_with_sandbox(cutoff)
            if self.fix_job_repository is not None
            else []
        )
        publish_fix_jobs = (
            await self.fix_job_repository.list_expired_with_publish_sandbox(cutoff)
            if self.fix_job_repository is not None
            else []
        )
        cleaned_jobs = 0
        skipped_jobs = 0
        freed_bytes = 0

        for review_job in review_jobs:
            cleaned_bytes = await self._cleanup_review_job_sandbox(
                review_job,
                sandbox_root,
            )
            if cleaned_bytes is None:
                skipped_jobs += 1
                continue

            cleaned_jobs += 1
            freed_bytes += cleaned_bytes

        for fix_job in fix_jobs:
            cleaned_bytes = await self._cleanup_fix_job_sandbox(
                fix_job,
                sandbox_root,
            )
            if cleaned_bytes is None:
                skipped_jobs += 1
                continue

            cleaned_jobs += 1
            freed_bytes += cleaned_bytes

        for fix_job in publish_fix_jobs:
            cleaned_bytes = await self._cleanup_fix_job_publish_sandbox(
                fix_job,
                sandbox_root,
            )
            if cleaned_bytes is None:
                skipped_jobs += 1
                continue

            cleaned_jobs += 1
            freed_bytes += cleaned_bytes

        return SandboxCleanupSummary(
            scanned_jobs=len(review_jobs) + len(fix_jobs) + len(publish_fix_jobs),
            cleaned_jobs=cleaned_jobs,
            skipped_jobs=skipped_jobs,
            freed_bytes=freed_bytes,
        )

    async def _cleanup_review_job_sandbox(
        self,
        review_job: ReviewJob,
        sandbox_root: Path,
    ) -> int | None:
        if review_job.sandbox_path is None:
            return None

        return await self._cleanup_sandbox_path(
            record_id=review_job.id,
            sandbox_path=Path(review_job.sandbox_path),
            sandbox_root=sandbox_root,
            record_label="review job",
            clear_sandbox_path=self.review_job_repository.clear_sandbox_path,
        )

    async def _cleanup_fix_job_sandbox(
        self,
        fix_job: FixJob,
        sandbox_root: Path,
    ) -> int | None:
        if fix_job.sandbox_path is None or self.fix_job_repository is None:
            return None

        return await self._cleanup_sandbox_path(
            record_id=fix_job.id,
            sandbox_path=Path(fix_job.sandbox_path),
            sandbox_root=sandbox_root,
            record_label="fix job",
            clear_sandbox_path=self.fix_job_repository.clear_sandbox_path,
        )

    async def _cleanup_fix_job_publish_sandbox(
        self,
        fix_job: FixJob,
        sandbox_root: Path,
    ) -> int | None:
        if fix_job.publish_sandbox_path is None or self.fix_job_repository is None:
            return None

        return await self._cleanup_sandbox_path(
            record_id=fix_job.id,
            sandbox_path=Path(fix_job.publish_sandbox_path),
            sandbox_root=sandbox_root,
            record_label="fix publish job",
            clear_sandbox_path=self.fix_job_repository.clear_publish_sandbox_path,
        )

    async def _cleanup_sandbox_path(
        self,
        *,
        record_id: UUID,
        sandbox_path: Path,
        sandbox_root: Path,
        record_label: str,
        clear_sandbox_path: Callable[[UUID], Awaitable[None]],
    ) -> int | None:
        if not is_path_inside_directory(sandbox_path, sandbox_root):
            logger.warning(
                "Skipping unsafe sandbox cleanup for %s %s path=%s",
                record_label,
                record_id,
                sandbox_path,
            )
            return None

        try:
            freed_bytes = calculate_path_size(sandbox_path)
        except OSError:
            logger.warning(
                "Unable to calculate sandbox size for %s %s path=%s",
                record_label,
                record_id,
                sandbox_path,
                exc_info=True,
            )
            freed_bytes = None

        try:
            if sandbox_path.exists():
                shutil.rmtree(sandbox_path)
        except OSError:
            logger.exception(
                "Failed to cleanup sandbox for %s %s path=%s",
                record_label,
                record_id,
                sandbox_path,
            )
            return None

        await clear_sandbox_path(record_id)
        logger.info(
            "Cleaned sandbox for %s %s path=%s freed_bytes=%s",
            record_label,
            record_id,
            sandbox_path,
            freed_bytes if freed_bytes is not None else "unknown",
        )
        return freed_bytes or 0


def is_path_inside_directory(path: Path, directory: Path) -> bool:
    """Return True when path resolves below directory, excluding directory itself."""

    resolved_path = path.expanduser().resolve()
    resolved_directory = directory.expanduser().resolve()
    if resolved_path == resolved_directory:
        return False

    try:
        resolved_path.relative_to(resolved_directory)
    except ValueError:
        return False

    return True


def calculate_path_size(path: Path) -> int:
    """Return total file size for a path, or zero when it no longer exists."""

    if not path.exists():
        return 0

    if path.is_file():
        return path.stat().st_size

    return sum(
        file_path.stat().st_size for file_path in path.rglob("*") if file_path.is_file()
    )
