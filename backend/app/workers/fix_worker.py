"""Background fix worker tasks."""

import logging
from uuid import UUID

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal, close_postgres_engine
from app.db.redis import close_redis_client
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.services.fix_pipeline.service import FixPipelineService
from app.workers.async_runtime import run_worker_coroutine
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.fix_worker.process_fix_job")
def process_fix_job(fix_job_id: str) -> dict[str, str]:
    """Run the patch-only fix pipeline for a queued job."""

    logger.info("Fix worker started job %s", fix_job_id)
    run_worker_coroutine(process_fix_job_async(UUID(fix_job_id)))
    return {"fix_job_id": fix_job_id, "status": "processed"}


async def process_fix_job_async(fix_job_id: UUID) -> None:
    """Build worker-scoped dependencies and process one fix job."""

    try:
        async with AsyncSessionLocal() as session:
            pipeline_service = FixPipelineService(
                settings=get_settings(),
                fix_job_repository=FixJobRepository(session),
                report_repository=ReportRepository(session),
            )
            await pipeline_service.run(fix_job_id)
    finally:
        await close_redis_client()
        await close_postgres_engine()
