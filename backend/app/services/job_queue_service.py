"""Queue adapter for review job dispatching."""

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class JobQueueService:
    """Dispatch review jobs to the background queue.

    Celery is introduced in P1.17. Until then, this adapter preserves the
    call site and logs the intended enqueue operation.
    """

    async def enqueue(self, job_id: UUID) -> None:
        """Log a queued review job until Celery is wired in."""

        logger.info("Review job enqueue stub called for job %s", job_id)
