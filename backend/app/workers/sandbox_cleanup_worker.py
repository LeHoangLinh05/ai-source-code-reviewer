"""Celery tasks for expired sandbox cleanup."""

import logging

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal, close_postgres_engine
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.services.sandbox_cleanup_service import (
    SandboxCleanupService,
    SandboxCleanupSummary,
)
from app.workers.async_runtime import run_worker_coroutine
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.sandbox_cleanup_worker.cleanup_expired_sandboxes")
def cleanup_expired_sandboxes() -> dict[str, int]:
    """Run one cleanup sweep for completed or failed review job sandboxes."""

    summary = run_worker_coroutine(cleanup_expired_sandboxes_async())
    return {
        "scanned_jobs": summary.scanned_jobs,
        "cleaned_jobs": summary.cleaned_jobs,
        "skipped_jobs": summary.skipped_jobs,
        "freed_bytes": summary.freed_bytes,
    }


async def cleanup_expired_sandboxes_async() -> SandboxCleanupSummary:
    """Build worker-scoped dependencies and run sandbox cleanup."""

    try:
        async with AsyncSessionLocal() as session:
            cleanup_service = SandboxCleanupService(
                settings=get_settings(),
                fix_job_repository=FixJobRepository(session),
                review_job_repository=ReviewJobRepository(session),
            )
            summary = await cleanup_service.cleanup_expired_sandboxes()
            logger.info(
                "Sandbox cleanup sweep finished scanned=%s cleaned=%s "
                "skipped=%s freed_bytes=%s",
                summary.scanned_jobs,
                summary.cleaned_jobs,
                summary.skipped_jobs,
                summary.freed_bytes,
            )
            return summary
    finally:
        await close_postgres_engine()
