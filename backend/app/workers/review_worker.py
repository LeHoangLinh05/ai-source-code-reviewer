"""Background review worker tasks."""

import logging
from uuid import UUID

from app.ai.rag.code_embedding import CodeEmbeddingStore, DisabledCodeEmbeddingStore
from app.core.config import get_settings
from app.db.mongodb import close_mongodb_client, get_mongodb_database
from app.db.postgres import AsyncSessionLocal, close_postgres_engine
from app.db.redis import close_redis_client
from app.models.review_job import ReviewJobStatus
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    CodeIndexManifestRepository,
    FileAnalysisResultRepository,
    RawStaticAnalysisOutputRepository,
    RepoSummaryResultRepository,
    ToolCallLogRepository,
)
from app.repositories.report_repository import ReportRepository
from app.repositories.repository_repository import RepositoryRepository
from app.repositories.review_job_repository import ReviewJobRepository
from app.services.code_indexing.service import CodeIndexingService
from app.services.review_jobs.notifications import publish_job_progress
from app.services.review_jobs.pipeline.service import (
    ReviewPipelineService,
    build_error_message,
)
from app.workers.async_runtime import run_worker_coroutine
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
    run_worker_coroutine(process_review_job_async(UUID(job_id)))
    return {"job_id": job_id, "status": "processed"}


async def process_review_job_async(job_id: UUID) -> None:
    """Build worker-scoped dependencies and process one review job."""

    try:
        async with AsyncSessionLocal() as session:
            settings = get_settings()
            database = get_mongodb_database()
            review_job_repository = ReviewJobRepository(session)
            code_embedding_store: CodeEmbeddingStore | DisabledCodeEmbeddingStore
            if settings.enable_code_semantic_search:
                try:
                    logger.info(
                        "Review worker initializing semantic code store for job %s "
                        "with provider=%s model=%s",
                        job_id,
                        settings.code_embedding_provider,
                        settings.code_embedding_model,
                    )
                    code_embedding_store = CodeEmbeddingStore(
                        persist_path=settings.rag_chroma_path,
                        model_name=settings.code_embedding_model,
                    )
                    logger.info(
                        "Review worker semantic code store initialized for job %s",
                        job_id,
                    )
                except Exception as error:
                    await _mark_worker_startup_failure(
                        job_id,
                        review_job_repository,
                        error,
                    )
                    return
            else:
                code_embedding_store = DisabledCodeEmbeddingStore()

            code_indexing_service = CodeIndexingService(
                settings=settings,
                chunk_repository=ChunkMetadataRepository(database),
                manifest_repository=CodeIndexManifestRepository(database),
                trace_repository=ToolCallLogRepository(database),
                embedding_store=code_embedding_store,
            )
            pipeline_service = ReviewPipelineService(
                settings=settings,
                review_job_repository=review_job_repository,
                repository_repository=RepositoryRepository(session),
                report_repository=ReportRepository(session),
                file_analysis_repository=FileAnalysisResultRepository(database),
                repo_summary_repository=RepoSummaryResultRepository(database),
                raw_static_repository=RawStaticAnalysisOutputRepository(database),
                code_indexing_service=code_indexing_service,
                code_embedding_store=code_embedding_store,
                postgres_session=session,
            )
            await pipeline_service.run(job_id)
    finally:
        if "code_embedding_store" in locals():
            code_embedding_store.close()
        await close_mongodb_client()
        await close_redis_client()
        await close_postgres_engine()


async def _mark_worker_startup_failure(
    job_id: UUID,
    review_job_repository: ReviewJobRepository,
    error: Exception,
) -> None:
    """Persist failures that happen before ReviewPipelineService can run."""

    error_message = build_error_message(error)
    logger.exception("Review worker failed to start job %s: %s", job_id, error_message)
    await review_job_repository.rollback()
    if await review_job_repository.get_by_id(job_id) is None:
        logger.info("Review job %s was removed before failure could persist", job_id)
        return

    await review_job_repository.mark_failed_by_id(
        job_id,
        error_message=error_message,
        progress=100,
    )
    await publish_job_progress(
        job_id,
        "failed",
        {
            "status": ReviewJobStatus.FAILED.value,
            "progress": 100,
            "message": error_message,
        },
    )
