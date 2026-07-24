"""ChromaDB storage and embeddings for repository source-code chunks."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from app.ai.rag.code_embedding_chunks import (
    CODE_CHUNKER_VERSION,
    CODE_EMBEDDING_MODEL_VERSION,
    UNKNOWN_CODE_EMBEDDING_DIMENSION,
    _can_send_chunk_to_remote,
    _chunk_id,
    _chunk_metadata,
    _common_index_generation_key,
    _common_repo_branch_key,
    _count_sensitive_chunks,
    _token_limited_batches,
    _truncate_embedding_text,
    build_content_hash,
    build_embedding_cache_id,
    build_index_generation_key,
    build_repo_branch_key,
    code_embedding_model_version,
    prepare_code_embedding_chunks,
)
from app.ai.rag.code_embedding_client import (
    _code_embedding_api_key,
    _code_embedding_base_url,
    _OpenAICompatibleCodeEmbedder,
)
from app.core.config import get_settings
from app.schemas.mongodb import ChunkMetadataDocument

logger = logging.getLogger(__name__)

CODE_CHUNKS_COLLECTION = "code_chunks"
CODE_EMBEDDING_DIMENSION = 1536

__all__ = [
    "CODE_CHUNKER_VERSION",
    "CODE_CHUNKS_COLLECTION",
    "CODE_EMBEDDING_DIMENSION",
    "CODE_EMBEDDING_MODEL_VERSION",
    "CodeEmbeddingBatchTrace",
    "CodeEmbeddingIndexSummary",
    "CodeEmbeddingStore",
    "CodeVectorQuery",
    "CodeVectorSearchResult",
    "DisabledCodeEmbeddingStore",
    "build_content_hash",
    "build_embedding_cache_id",
    "build_index_generation_key",
    "build_repo_branch_key",
    "code_embedding_model_version",
    "prepare_code_embedding_chunks",
]


@dataclass(slots=True, frozen=True)
class CodeVectorSearchResult:
    """One source-code chunk returned by the vector index."""

    content: str
    metadata: dict[str, object]
    score: float


@dataclass(slots=True, frozen=True)
class CodeEmbeddingBatchTrace:
    """Timing and token metadata for one embedding batch."""

    batch_number: int
    total_batches: int
    file_paths: list[str]
    chunk_count: int
    duration_ms: int
    estimated_input_tokens: int
    token_usage: dict[str, int] | None = None


@dataclass(slots=True, frozen=True)
class CodeVectorQuery:
    """One vector search request sharing the same embedding adapter."""

    query: str
    n_results: int
    where: dict[str, object]


@dataclass(slots=True, frozen=True)
class CodeEmbeddingIndexSummary:
    """Summary returned after indexing source-code chunks."""

    indexed_count: int
    duration_ms: int
    batches: list[CodeEmbeddingBatchTrace]
    cache_hit_count: int = 0
    embedded_count: int = 0
    pruned_count: int = 0
    total_current_count: int = 0
    skipped_sensitive_count: int = 0


class CodeEmbedder(Protocol):
    """Embedding adapter used by the source-code vector store."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


class CodeEmbeddingStore:
    """Embed full source chunks and persist them in a repo-branch cache."""

    def __init__(
        self,
        *,
        persist_path: str | Path | None = None,
        model_name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        dimension: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = provider or settings.code_embedding_provider
        self.model_name = model_name or settings.code_embedding_model
        self.dimension = (
            settings.code_embedding_dimension if dimension is None else dimension
        )
        self.batch_size = batch_size or settings.code_embedding_batch_size
        self.max_item_tokens = settings.code_embedding_max_item_tokens
        self.max_batch_tokens = settings.code_embedding_max_batch_tokens
        self._validate_provider_config()

        self.persist_path = Path(persist_path or settings.rag_chroma_path)
        self.persist_path.mkdir(parents=True, exist_ok=True)
        self._client = _build_chroma_client(self.persist_path)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name(),
            metadata=self._collection_metadata(),
            embedding_function=None,
        )
        self._validate_collection_model()
        self._embedder = self._build_embedder(
            settings=settings,
            base_url=base_url,
            api_key=api_key,
        )

    def index_chunks(
        self,
        chunks: list[ChunkMetadataDocument],
        *,
        repository_id: str | UUID | None = None,
        branch: str | None = None,
        commit_sha: str | None = None,
    ) -> CodeEmbeddingIndexSummary:
        """Embed only new source chunks and refresh current cache metadata."""

        indexable_chunks = [
            chunk for chunk in chunks if isinstance(chunk.chunk_text, str)
        ]
        if not indexable_chunks:
            logger.info("No source chunks available for semantic code indexing")
            return CodeEmbeddingIndexSummary(
                indexed_count=0,
                duration_ms=0,
                batches=[],
                total_current_count=0,
            )

        started_at = time.perf_counter()
        prepared_chunks = self._prepare_indexable_chunks(
            indexable_chunks,
            repository_id=repository_id,
            branch=branch,
            commit_sha=commit_sha,
        )
        skipped_sensitive_count = _count_sensitive_chunks(prepared_chunks)
        prepared_chunks = [
            chunk for chunk in prepared_chunks if _can_send_chunk_to_remote(chunk)
        ]
        repo_branch_key = _common_repo_branch_key(prepared_chunks)
        index_generation_key = _common_index_generation_key(prepared_chunks)
        index_scope_key = index_generation_key or repo_branch_key
        logger.info(
            "Semantic code indexing preparing %d chunks with provider=%s model=%s "
            "batch_size=%d repo_branch_key=%s index_generation_key=%s "
            "skipped_sensitive=%d",
            len(prepared_chunks),
            self.provider,
            self.model_name,
            self.batch_size,
            repo_branch_key or "legacy",
            index_generation_key or "legacy",
            skipped_sensitive_count,
        )

        existing_cache_ids = (
            self._existing_cache_ids(index_scope_key) if index_scope_key else set()
        )
        chunks_to_embed = [
            chunk
            for chunk in prepared_chunks
            if _chunk_id(chunk) not in existing_cache_ids
        ]
        cached_chunks = [
            chunk for chunk in prepared_chunks if _chunk_id(chunk) in existing_cache_ids
        ]
        self._refresh_cached_chunks(cached_chunks)

        batches_to_embed = _token_limited_batches(
            chunks_to_embed,
            max_items=self.batch_size,
            max_tokens=self.max_batch_tokens,
            max_item_tokens=self.max_item_tokens,
        )
        total_batches = len(batches_to_embed)
        embedded_count = 0
        batch_traces: list[CodeEmbeddingBatchTrace] = []
        for batch_number, batch_chunks in enumerate(batches_to_embed, start=1):
            batch_trace = self._index_chunk_batch(
                batch_chunks,
                batch_number=batch_number,
                total_batches=total_batches,
            )
            embedded_count += batch_trace.chunk_count
            batch_traces.append(batch_trace)

        pruned_count = (
            self._prune_stale_cache_ids(
                index_generation_key=index_scope_key,
                existing_cache_ids=existing_cache_ids,
                current_cache_ids={_chunk_id(chunk) for chunk in prepared_chunks},
            )
            if index_scope_key
            else 0
        )
        duration_ms = _duration_ms(started_at)
        logger.info(
            "Semantic code indexing finished for %d current chunks in Chroma "
            "collection %s in %.2fs: embedded=%d cache_hits=%d pruned=%d",
            len(prepared_chunks),
            self._collection_name(),
            duration_ms / 1000,
            embedded_count,
            len(cached_chunks),
            pruned_count,
        )
        return CodeEmbeddingIndexSummary(
            indexed_count=embedded_count,
            duration_ms=duration_ms,
            batches=batch_traces,
            cache_hit_count=len(cached_chunks),
            embedded_count=embedded_count,
            pruned_count=pruned_count,
            total_current_count=len(prepared_chunks),
            skipped_sensitive_count=skipped_sensitive_count,
        )

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]:
        """Search source chunks using an explicit metadata filter."""

        if n_results <= 0:
            return []

        return self.query_many(
            [CodeVectorQuery(query=query, n_results=n_results, where=where)]
        )[0]

    def query_many(
        self,
        queries: list[CodeVectorQuery],
    ) -> list[list[CodeVectorSearchResult]]:
        """Search source chunks for many query texts with exact-order output."""

        if not queries:
            return []

        query_texts = [query.query for query in queries]
        embed_queries = getattr(self._embedder, "embed_queries", None)
        raw_embeddings = (
            embed_queries(query_texts)
            if callable(embed_queries)
            else [self._embedder.embed_query(query_text) for query_text in query_texts]
        )
        query_embeddings = _validated_embeddings(
            raw_embeddings,
            expected_dimension=self._expected_dimension(),
        )
        if len(query_embeddings) != len(queries):
            raise RuntimeError("Code embedding model returned an unexpected batch size")

        grouped_results: list[list[CodeVectorSearchResult]] = [[] for _ in queries]
        for group in _query_groups(queries):
            max_results = max(queries[index].n_results for index in group)
            if max_results <= 0:
                continue
            result = self._collection.query(
                query_embeddings=[query_embeddings[index] for index in group],
                n_results=max_results,
                where=queries[group[0]].where,
                include=["documents", "metadatas", "distances"],
            )
            documents_by_query = result.get("documents", [])
            metadatas_by_query = result.get("metadatas", [])
            distances_by_query = result.get("distances", [])
            for offset, query_index in enumerate(group):
                grouped_results[query_index] = _vector_results_from_query_payload(
                    documents_by_query[offset]
                    if offset < len(documents_by_query)
                    else [],
                    metadatas_by_query[offset]
                    if offset < len(metadatas_by_query)
                    else [],
                    distances_by_query[offset]
                    if offset < len(distances_by_query)
                    else [],
                )[: queries[query_index].n_results]

        return grouped_results

    def delete_job(self, job_id: str) -> None:
        """Delete legacy job-scoped vectors left by earlier index versions."""

        normalized_job_id = job_id.strip()
        if not normalized_job_id:
            raise ValueError("job_id is required to clean code_chunks")

        self._collection.delete(where={"job_id": normalized_job_id})

    def close(self) -> None:
        """Close reusable remote clients owned by this store."""

        close = getattr(self._embedder, "close", None)
        if callable(close):
            close()

    def _prepare_indexable_chunks(
        self,
        chunks: list[ChunkMetadataDocument],
        *,
        repository_id: str | UUID | None,
        branch: str | None,
        commit_sha: str | None,
    ) -> list[ChunkMetadataDocument]:
        if repository_id is None and branch is None:
            if all(chunk.embedding_cache_id for chunk in chunks):
                return chunks
            return chunks

        return prepare_code_embedding_chunks(
            chunks,
            repository_id=repository_id,
            branch=branch,
            commit_sha=commit_sha,
            provider=self.provider,
            model_name=self.model_name,
            model_version=self._model_version(),
            dimension=self._expected_dimension(),
        )

    def _existing_cache_ids(self, index_generation_key: str) -> set[str]:
        result = self._collection.get(
            where={"index_generation_key": index_generation_key},
            include=["metadatas"],
        )
        ids = result.get("ids", [])
        return {str(item) for item in ids if isinstance(item, str)}

    def _refresh_cached_chunks(self, chunks: list[ChunkMetadataDocument]) -> None:
        if not chunks:
            return

        self._collection.update(
            ids=[_chunk_id(chunk) for chunk in chunks],
            metadatas=[_chunk_metadata(chunk) for chunk in chunks],
        )

    def _prune_stale_cache_ids(
        self,
        *,
        index_generation_key: str | None,
        existing_cache_ids: set[str],
        current_cache_ids: set[str],
    ) -> int:
        _ = index_generation_key
        stale_cache_ids = sorted(existing_cache_ids - current_cache_ids)
        if not stale_cache_ids:
            return 0

        self._collection.delete(ids=stale_cache_ids)
        return len(stale_cache_ids)

    def _validate_collection_model(self) -> None:
        metadata = self._collection.metadata or {}
        expected_metadata: dict[str, str | int] = {
            "embedding_provider": self.provider,
            "embedding_model": self.model_name,
            "embedding_model_version": self._model_version(),
        }

        expected_dimension = self._expected_dimension()
        if expected_dimension is not None:
            expected_metadata["embedding_dimension"] = expected_dimension

        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise RuntimeError(
                "code_chunks has incompatible embedding model metadata; "
                "rebuild the collection before indexing or searching"
            )

    def _validate_provider_config(self) -> None:
        if self.provider not in {
            "openai",
            "mistral",
            "openrouter",
        }:
            raise ValueError(f"Unsupported code embedding provider: {self.provider}")

        if self.batch_size <= 0:
            raise ValueError("code_embedding_batch_size must be greater than zero")

        if self.dimension < UNKNOWN_CODE_EMBEDDING_DIMENSION:
            raise ValueError("code_embedding_dimension must be zero or greater")

    def _build_embedder(
        self,
        *,
        settings: Any,
        base_url: str | None,
        api_key: str | None,
    ) -> CodeEmbedder:
        resolved_api_key = api_key or _code_embedding_api_key(
            provider=self.provider,
            settings=settings,
        )
        resolved_base_url = base_url or _code_embedding_base_url(
            provider=self.provider,
            settings=settings,
        )
        return _OpenAICompatibleCodeEmbedder(
            provider=self.provider,
            model_name=self.model_name,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            output_dimension=self._expected_dimension(),
            max_retries=settings.code_embedding_max_retries,
            retry_base_delay_seconds=settings.code_embedding_retry_base_delay_seconds,
        )

    def _index_chunk_batch(
        self,
        chunks: list[ChunkMetadataDocument],
        *,
        batch_number: int,
        total_batches: int,
    ) -> CodeEmbeddingBatchTrace:
        documents = [
            _truncate_embedding_text(
                str(chunk.chunk_text),
                max_tokens=self.max_item_tokens,
            )
            for chunk in chunks
        ]
        batch_started_at = time.perf_counter()
        logger.info(
            "Generating semantic code embedding batch %d/%d with %d documents",
            batch_number,
            total_batches,
            len(documents),
        )
        embeddings = _validated_embeddings(
            self._embedder.embed_documents(documents),
            expected_dimension=self._expected_dimension(),
        )
        token_usage = getattr(self._embedder, "last_token_usage", None)
        if len(embeddings) != len(chunks):
            raise RuntimeError("Code embedding model returned an unexpected batch size")

        logger.info(
            "Generated semantic code embedding batch %d/%d in %.2fs",
            batch_number,
            total_batches,
            time.perf_counter() - batch_started_at,
        )
        upsert_started_at = time.perf_counter()
        self._collection.upsert(
            ids=[_chunk_id(chunk) for chunk in chunks],
            documents=documents,
            metadatas=[_chunk_metadata(chunk) for chunk in chunks],
            embeddings=embeddings,
        )
        logger.info(
            "Upserted semantic code embedding batch %d/%d to Chroma in %.2fs",
            batch_number,
            total_batches,
            time.perf_counter() - upsert_started_at,
        )
        batch_trace = CodeEmbeddingBatchTrace(
            batch_number=batch_number,
            total_batches=total_batches,
            file_paths=sorted({chunk.file_path for chunk in chunks}),
            chunk_count=len(chunks),
            duration_ms=_duration_ms(batch_started_at),
            estimated_input_tokens=sum(chunk.token_count for chunk in chunks),
            token_usage=token_usage if isinstance(token_usage, dict) else None,
        )
        return batch_trace

    def _collection_name(self) -> str:
        safe_model_name = "".join(
            character if character.isalnum() else "_"
            for character in self.model_name.lower()
        ).strip("_")
        return f"{CODE_CHUNKS_COLLECTION}_{self.provider}_{safe_model_name}"[:63]

    def _collection_metadata(self) -> dict[str, str | int]:
        metadata: dict[str, str | int] = {
            "hnsw:space": "cosine",
            "embedding_provider": self.provider,
            "embedding_model": self.model_name,
            "embedding_model_version": self._model_version(),
        }

        expected_dimension = self._expected_dimension()
        if expected_dimension is not None:
            metadata["embedding_dimension"] = expected_dimension

        return metadata

    def _model_version(self) -> str:
        return code_embedding_model_version(self.provider)

    def _expected_dimension(self) -> int | None:
        if self.dimension == UNKNOWN_CODE_EMBEDDING_DIMENSION:
            return None

        return self.dimension


class DisabledCodeEmbeddingStore(CodeEmbeddingStore):
    """No-op code vector store used when semantic code search is disabled."""

    def __init__(self) -> None:
        """Avoid loading Chroma or embedding models when semantic search is off."""

    def index_chunks(
        self,
        chunks: list[ChunkMetadataDocument],
        *,
        repository_id: str | UUID | None = None,
        branch: str | None = None,
        commit_sha: str | None = None,
    ) -> CodeEmbeddingIndexSummary:
        """Skip source-code embedding while preserving normal chunk persistence."""

        _ = repository_id, branch, commit_sha
        logger.info(
            "Semantic code search disabled; skipping vector indexing for %d chunks",
            len(chunks),
        )
        return CodeEmbeddingIndexSummary(indexed_count=0, duration_ms=0, batches=[])

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]:
        """Return no semantic matches when the optional index is disabled."""

        _ = query, n_results, where
        return []

    def query_many(
        self,
        queries: list[CodeVectorQuery],
    ) -> list[list[CodeVectorSearchResult]]:
        """Return no semantic matches when the optional index is disabled."""

        return [[] for _query in queries]

    def delete_job(self, job_id: str) -> None:
        """No-op cleanup for disabled code vector storage."""

        _ = job_id

    def close(self) -> None:
        """No-op cleanup for disabled code vector storage."""


def _build_chroma_client(persist_path: Path) -> Any:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError("chromadb is required for code semantic search") from error

    return chromadb.PersistentClient(path=str(persist_path))


def _vector_results_from_query_payload(
    documents: list[object],
    metadatas: list[object],
    distances: list[object],
) -> list[CodeVectorSearchResult]:
    results: list[CodeVectorSearchResult] = []
    for content, raw_metadata, distance in zip(
        documents,
        metadatas,
        distances,
        strict=True,
    ):
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        results.append(
            CodeVectorSearchResult(
                content=str(content),
                metadata=dict(metadata),
                score=max(0.0, 1.0 - float(str(distance))),
            )
        )
    return results


def _query_groups(queries: list[CodeVectorQuery]) -> list[list[int]]:
    groups_by_where: dict[str, list[int]] = {}
    for index, query in enumerate(queries):
        key = json.dumps(query.where, sort_keys=True, default=str)
        groups_by_where.setdefault(key, []).append(index)

    return list(groups_by_where.values())


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _validated_embeddings(
    values: object,
    *,
    expected_dimension: int | None = CODE_EMBEDDING_DIMENSION,
) -> list[list[float]]:
    if not isinstance(values, list):
        raise RuntimeError("Code embedding model returned an invalid tensor")

    embeddings: list[list[float]] = []
    for value in values:
        if not isinstance(value, list) or not value:
            raise RuntimeError("Code embedding model returned an invalid vector")

        if expected_dimension is not None and len(value) != expected_dimension:
            raise RuntimeError(
                "Code embedding dimension changed; rebuild code_chunks before use"
            )
        embeddings.append([float(item) for item in value])

    return embeddings
