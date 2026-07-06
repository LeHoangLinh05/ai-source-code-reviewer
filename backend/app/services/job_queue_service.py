"""Queue adapter for review job dispatching."""

import logging
from uuid import UUID

from celery.exceptions import CeleryError  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from app.core.exceptions import ServiceUnavailableError
from app.workers.review_worker import process_review_job

logger = logging.getLogger(__name__)


class JobQueueService:
    """Dispatch review jobs to the background queue."""

    async def enqueue(self, job_id: UUID) -> None:
        """Send a review job to the Celery worker queue."""

        try:
            async_result = process_review_job.delay(str(job_id))
        except (CeleryError, OperationalError) as error:
            raise ServiceUnavailableError("Review job queue is unavailable") from error

        logger.info(
            "Review job %s enqueued as Celery task %s",
            job_id,
            async_result.id,
        )
