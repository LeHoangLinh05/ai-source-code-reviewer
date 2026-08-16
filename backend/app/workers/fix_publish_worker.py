"""Background tasks for publishing generated fix jobs."""

import logging
from uuid import UUID

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal, close_postgres_engine
from app.db.redis import close_redis_client
from app.repositories.fix_audit_log_repository import FixAuditLogRepository
from app.repositories.fix_job_repository import FixJobRepository
from app.repositories.report_repository import ReportRepository
from app.services.fix_jobs.publish_pipeline import FixPublishPipelineService
from app.services.git_provider.github import GitHubProvider
from app.workers.async_runtime import run_worker_coroutine
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.fix_publish_worker.publish_fix_job")
def publish_fix_job(
    fix_job_id: str,
    strategy: str,
    allow_failed_validation: bool,
) -> dict[str, str]:
    """Run the pull-request publish pipeline for an approved fix job."""

    logger.info("Fix publish worker started job %s", fix_job_id)
    del strategy
    run_worker_coroutine(
        publish_fix_job_async(
            UUID(fix_job_id),
            allow_failed_validation=allow_failed_validation,
        )
    )
    return {"fix_job_id": fix_job_id, "status": "published"}


async def publish_fix_job_async(
    fix_job_id: UUID,
    *,
    allow_failed_validation: bool,
) -> None:
    """Build worker-scoped dependencies and publish one fix job."""

    try:
        async with AsyncSessionLocal() as session:
            settings = get_settings()
            pipeline_service = FixPublishPipelineService(
                settings=settings,
                fix_job_repository=FixJobRepository(session),
                audit_log_repository=FixAuditLogRepository(session),
                report_repository=ReportRepository(session),
                github_provider=GitHubProvider(settings=settings),
            )
            await pipeline_service.run(
                fix_job_id=fix_job_id,
                allow_failed_validation=allow_failed_validation,
            )
    finally:
        await close_redis_client()
        await close_postgres_engine()
