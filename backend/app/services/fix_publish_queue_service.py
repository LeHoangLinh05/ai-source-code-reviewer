"""Queue adapter for publishing generated fix jobs."""

import logging
from uuid import UUID

from celery.exceptions import CeleryError  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from app.core.exceptions import ServiceUnavailableError
from app.schemas.fix_job import PublishStrategy
from app.workers.fix_publish_worker import publish_fix_job

logger = logging.getLogger(__name__)


class FixPublishQueueService:
    """Dispatch approved fix publishes to the background queue."""

    async def enqueue(
        self,
        *,
        fix_job_id: UUID,
        strategy: PublishStrategy,
        allow_failed_validation: bool,
    ) -> None:
        """Send a publish job to the Celery worker queue."""

        try:
            async_result = publish_fix_job.apply_async(
                args=[
                    str(fix_job_id),
                    strategy,
                    allow_failed_validation,
                ],
                task_id=f"publish:{fix_job_id}",
            )
        except (CeleryError, OperationalError) as error:
            raise ServiceUnavailableError("Fix publish queue is unavailable") from error

        logger.info(
            "Fix publish %s enqueued as Celery task %s",
            fix_job_id,
            async_result.id,
        )
