"""Build and persist source chunks for semantic code retrieval."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias
from uuid import UUID, uuid4

from app.ai.rag.code_embedding import (
    CodeEmbeddingBatchTrace,
    CodeEmbeddingIndexSummary,
    CodeEmbeddingStore,
    code_embedding_model_version,
    prepare_code_embedding_chunks,
)
from app.core.config import Settings
from app.models.review_job import ReviewJob
from app.repositories.mongodb_repository import (
    ChunkMetadataRepository,
    CodeIndexManifestRepository,
    ToolCallLogRepository,
)
from app.schemas.mongodb import (
    ChunkMetadataDocument,
    CodeIndexManifestDocument,
    ToolCallLogDocument,
)
from app.schemas.normalized_issue import NormalizedIssue
from app.services.code_indexing.documents import build_source_chunk_documents

logger = logging.getLogger(__name__)

IndexStatus: TypeAlias = Literal["BUILDING", "INDEXED", "FAILED"]
BUILDING_INDEX_STATUS: IndexStatus = "BUILDING"
INDEXED_INDEX_STATUS: IndexStatus = "INDEXED"
FAILED_INDEX_STATUS: IndexStatus = "FAILED"


class CodeIndexingService:
    """Coordinate source chunk persistence and semantic vector indexing."""

    def __init__(
        self,
        *,
        settings: Settings,
        chunk_repository: ChunkMetadataRepository,
        manifest_repository: CodeIndexManifestRepository,
        trace_repository: ToolCallLogRepository,
        embedding_store: CodeEmbeddingStore,
    ) -> None:
        self.settings = settings
        self.chunk_repository = chunk_repository
        self.manifest_repository = manifest_repository
        self.trace_repository = trace_repository
        self.embedding_store = embedding_store

    async def index(
        self,
        *,
        review_job: ReviewJob,
        sandbox_path: Path,
        filtered_files: list[Path],
        issues: list[NormalizedIssue],
    ) -> None:
        """Create source chunks and persist their searchable representations."""

        started_at = time.perf_counter()
        chunk_documents = build_source_chunk_documents(
            job_id=review_job.id,
            sandbox_path=sandbox_path,
            filtered_files=filtered_files,
            issues=issues,
        )
        branch = get_review_branch(review_job)
        chunks_to_embed = prepare_code_embedding_chunks(
            chunk_documents,
            repository_id=review_job.repository_id,
            branch=branch,
            commit_sha=review_job.commit_sha,
            provider=self.settings.code_embedding_provider,
            model_name=self.settings.code_embedding_model,
            model_version=code_embedding_model_version(
                self.settings.code_embedding_provider
            ),
            dimension=self.settings.code_embedding_dimension,
        )
        await self._write_manifest(
            review_job=review_job,
            chunks=chunks_to_embed,
            status=BUILDING_INDEX_STATUS,
        )
        inserted_count, roundtrips = await self.chunk_repository.replace_for_job(
            job_id=review_job.id,
            documents=chunks_to_embed,
            batch_size=self.settings.mongodb_chunk_batch_size,
        )
        logger.info(
            "Review job %s persisted %d source chunks in %.2fs using %d "
            "MongoDB write roundtrips",
            review_job.id,
            inserted_count,
            time.perf_counter() - started_at,
            roundtrips,
        )
        await self._index_embeddings(review_job, branch, chunks_to_embed)

    async def _index_embeddings(
        self,
        review_job: ReviewJob,
        branch: str,
        chunks: list[ChunkMetadataDocument],
    ) -> None:
        started_at = time.perf_counter()
        logger.info(
            "Review job %s semantic code indexing started for %d chunks",
            review_job.id,
            len(chunks),
        )
        try:
            summary = await asyncio.to_thread(
                self.embedding_store.index_chunks,
                chunks,
                repository_id=review_job.repository_id,
                branch=branch,
                commit_sha=review_job.commit_sha,
            )
        except Exception as error:
            await self._write_manifest(
                review_job=review_job,
                chunks=chunks,
                status=FAILED_INDEX_STATUS,
                error_message=str(error),
            )
            raise

        await self._write_manifest(
            review_job=review_job,
            chunks=chunks,
            status=INDEXED_INDEX_STATUS,
        )
        await self._write_embedding_traces(review_job.id, summary)
        logger.info(
            "Review job %s semantic code indexing finished in %.2fs",
            review_job.id,
            time.perf_counter() - started_at,
        )

    async def _write_manifest(
        self,
        *,
        review_job: ReviewJob,
        chunks: list[ChunkMetadataDocument],
        status: IndexStatus,
        error_message: str | None = None,
    ) -> None:
        if not chunks:
            return

        first_chunk = chunks[0]
        if (
            review_job.repository_id is None
            or not first_chunk.branch
            or not first_chunk.commit_sha
            or not first_chunk.repo_branch_key
            or not first_chunk.index_generation_key
        ):
            return

        await self.manifest_repository.upsert_for_job(
            CodeIndexManifestDocument(
                job_id=review_job.id,
                repository_id=review_job.repository_id,
                branch=first_chunk.branch,
                commit_sha=first_chunk.commit_sha,
                repo_branch_key=first_chunk.repo_branch_key,
                index_generation_key=first_chunk.index_generation_key,
                status=status,
                chunk_count=len(chunks),
                updated_at=datetime.now(UTC),
                error_message=error_message,
            )
        )

    async def _write_embedding_traces(
        self,
        job_id: UUID,
        summary: CodeEmbeddingIndexSummary,
    ) -> None:
        session_id = uuid4()
        if not summary.batches:
            await self.trace_repository.insert_one(
                self._build_cache_summary_trace(job_id, session_id, summary)
            )
            return

        for sequence, batch in enumerate(summary.batches, start=1):
            await self.trace_repository.insert_one(
                self._build_batch_trace(
                    job_id=job_id,
                    session_id=session_id,
                    sequence=sequence,
                    batch=batch,
                    summary=summary,
                )
            )

    def _build_cache_summary_trace(
        self,
        job_id: UUID,
        session_id: UUID,
        summary: CodeEmbeddingIndexSummary,
    ) -> ToolCallLogDocument:
        return ToolCallLogDocument(
            job_id=job_id,
            session_id=session_id,
            agent_type="review",
            sequence=1,
            tool_name="code_embedding_cache_summary",
            called_at=datetime.now(UTC),
            duration_ms=max(0, summary.duration_ms),
            input={"total_current_count": summary.total_current_count},
            output={
                "status": "ok",
                "embedded_count": summary.embedded_count,
                "cache_hit_count": summary.cache_hit_count,
                "pruned_count": summary.pruned_count,
            },
            event_type="embedding",
            provider=self.settings.code_embedding_provider,
            model=self.settings.code_embedding_model,
            phase="code_embedding",
            token_usage=None,
            status="ok",
            metadata={"source": "code_indexing_service"},
        )

    def _build_batch_trace(
        self,
        *,
        job_id: UUID,
        session_id: UUID,
        sequence: int,
        batch: CodeEmbeddingBatchTrace,
        summary: CodeEmbeddingIndexSummary,
    ) -> ToolCallLogDocument:
        token_usage = {"estimated_input_tokens": batch.estimated_input_tokens}
        if batch.token_usage is not None:
            token_usage.update(batch.token_usage)

        return ToolCallLogDocument(
            job_id=job_id,
            session_id=session_id,
            agent_type="review",
            sequence=sequence,
            tool_name="code_embedding_batch",
            called_at=datetime.now(UTC),
            duration_ms=max(0, batch.duration_ms),
            input={
                "batch_number": batch.batch_number,
                "total_batches": batch.total_batches,
                "file_paths": batch.file_paths,
            },
            output={
                "status": "ok",
                "chunk_count": batch.chunk_count,
                "embedded_count": summary.embedded_count,
                "cache_hit_count": summary.cache_hit_count,
                "pruned_count": summary.pruned_count,
                "total_current_count": summary.total_current_count,
            },
            event_type="embedding",
            provider=self.settings.code_embedding_provider,
            model=self.settings.code_embedding_model,
            phase="code_embedding",
            token_usage=token_usage,
            status="ok",
            metadata={"source": "code_indexing_service"},
        )


def get_review_branch(review_job: ReviewJob) -> str:
    """Return the exact branch name used for this review."""

    return (review_job.branch or review_job.repository.default_branch).strip()
