"""Dispatch fix jobs to the worker queue."""

import logging
from uuid import UUID

from celery.exceptions import CeleryError  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from app.core.exceptions import ServiceUnavailableError
from app.workers.fix_worker import process_fix_job

logger = logging.getLogger(__name__)


class FixJobQueueService:
    """Dispatch fix jobs to the background queue."""

    async def enqueue(self, fix_job_id: UUID) -> None:
        """Send a fix job to the Celery worker queue."""

        try:
            async_result = process_fix_job.apply_async(
                args=[str(fix_job_id)],
                task_id=f"fix:{fix_job_id}",
            )
        except (CeleryError, OperationalError) as error:
            raise ServiceUnavailableError("Fix job queue is unavailable") from error

        logger.info(
            "Fix job %s enqueued as Celery task %s",
            fix_job_id,
            async_result.id,
        )
