"""Background review worker tasks."""

import asyncio
import logging
from uuid import UUID

from app.core.config import get_settings
from app.db.mongodb import close_mongodb_client, get_mongodb_database
from app.db.postgres import AsyncSessionLocal, close_postgres_engine
from app.db.redis import close_redis_client
from app.repositories.mongodb_repository import (
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.services.review_pipeline_service import ReviewPipelineService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.review_worker.ping")
def ping() -> str:
    """Return a simple response for worker connectivity checks."""

    return "pong"


@celery_app.task(name="app.workers.review_worker.process_review_job")
def process_review_job(job_id: str) -> dict[str, str]:
    """Run the full review pipeline for a queued job."""

    logger.info("Review worker started job %s", job_id)
    asyncio.run(process_review_job_async(UUID(job_id)))
    return {"job_id": job_id, "status": "processed"}


async def process_review_job_async(job_id: UUID) -> None:
    """Build worker-scoped dependencies and process one review job."""

    try:
        async with AsyncSessionLocal() as session:
            database = get_mongodb_database()
            pipeline_service = ReviewPipelineService(
                settings=get_settings(),
                review_job_repository=ReviewJobRepository(session),
                repository_repository=RepositoryRepository(session),
                report_repository=ReportRepository(session),
                file_analysis_repository=FileAnalysisResultRepository(database),
                raw_static_repository=RawStaticAnalysisOutputRepository(database),
            )
            await pipeline_service.run(job_id)
    finally:
        await close_mongodb_client()
        await close_redis_client()
        await close_postgres_engine()
