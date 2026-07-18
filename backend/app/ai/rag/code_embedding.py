"""ChromaDB storage and embeddings for repository source-code chunks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Mapping, Protocol
from uuid import UUID

from app.core.config import get_settings
from app.schemas.mongodb import ChunkMetadataDocument

logger = logging.getLogger(__name__)

CODE_CHUNKS_COLLECTION = "code_chunks"
CODE_EMBEDDING_MODEL_VERSION = "remote-api-v1"
CODE_EMBEDDING_DIMENSION = 1536
UNKNOWN_CODE_EMBEDDING_DIMENSION = 0
CODE_CHUNKER_VERSION = "v1"


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


class _OpenAICompatibleCodeEmbedder:
    """Remote embedding adapter for OpenAI-compatible embedding APIs."""

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        api_key: str,
        base_url: str,
        output_dimension: int | None,
        max_retries: int,
        retry_base_delay_seconds: float,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.output_dimension = output_dimension
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.last_token_usage: dict[str, int] | None = None
        self._client: Any | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="passage")

    def embed_query(self, text: str) -> list[float]:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="query")

    def close(self) -> None:
        """Close the reusable HTTP client if it has been opened."""

        if self._client is None:
            return

        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._client = None

    def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []

        try:
            import httpx
        except ImportError as error:
            raise RuntimeError(
                "httpx is required for remote code semantic search embeddings"
            ) from error

        payload: dict[str, object] = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float",
        }
        _ = input_type
        if self.provider == "mistral":
            if self.output_dimension is not None:
                payload["output_dimension"] = self.output_dimension
            payload["output_dtype"] = "float"

        if self._client is None:
            self._client = httpx.Client(timeout=60.0)

        response = self._post_with_retries(self._client, payload)

        response_payload = response.json()
        self.last_token_usage = _token_usage_from_response(response_payload)
        return _embeddings_from_response(response_payload)

    def _post_with_retries(self, client: Any, payload: dict[str, object]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self.headers,
                json=payload,
            )
            try:
                response.raise_for_status()
                return response
            except Exception as error:
                last_error = error
                if not _should_retry_embedding_response(
                    response, attempt, self.max_retries
                ):
                    detail = _embedding_error_detail(response)
                    raise RuntimeError(
                        f"{self.provider} code embedding request failed: "
                        f"HTTP {response.status_code}{detail}"
                    ) from error

                delay_seconds = _embedding_retry_delay_seconds(
                    response=response,
                    attempt=attempt,
                    base_delay_seconds=self.retry_base_delay_seconds,
                )
                logger.warning(
                    "%s code embedding request failed with HTTP %s; retrying in %.1fs",
                    self.provider,
                    response.status_code,
                    delay_seconds,
                )
                time.sleep(delay_seconds)

        raise RuntimeError(
            f"{self.provider} code embedding request failed after retries"
        ) from last_error


def _build_chroma_client(persist_path: Path) -> Any:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError("chromadb is required for code semantic search") from error

    return chromadb.PersistentClient(path=str(persist_path))


def _code_embedding_api_key(*, provider: str, settings: Any) -> str:
    if provider == "mistral":
        secret = settings.mistral_api_key
    elif provider == "openrouter":
        secret = settings.openrouter_api_key
    else:
        secret = settings.openai_api_key
    api_key = secret.get_secret_value() if secret is not None else ""
    if not api_key:
        raise RuntimeError(f"{provider} API key is required for code embeddings")

    return api_key


def _code_embedding_base_url(*, provider: str, settings: Any) -> str:
    if settings.code_embedding_base_url:
        return str(settings.code_embedding_base_url)

    if provider == "mistral":
        return str(settings.mistral_base_url)

    if provider == "openrouter":
        return str(settings.openrouter_base_url)

    return "https://api.openai.com/v1"


def _should_retry_embedding_response(
    response: Any,
    attempt: int,
    max_retries: int,
) -> bool:
    if attempt >= max_retries:
        return False

    status_code = getattr(response, "status_code", 0)
    return status_code == 429 or 500 <= status_code <= 599


def _embedding_retry_delay_seconds(
    *,
    response: Any,
    attempt: int,
    base_delay_seconds: float,
) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return retry_after

    return min(base_delay_seconds * (2**attempt), 60.0)


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    value = headers.get("retry-after")
    if value is None:
        value = headers.get("Retry-After")
    if value is None:
        return None

    try:
        delay = float(value)
    except ValueError:
        return None

    return max(0.0, delay)


def _embedding_error_detail(response: Any) -> str:
    text = getattr(response, "text", "")
    if not isinstance(text, str) or not text.strip():
        return ""

    compact_text = " ".join(text.split())
    return f": {compact_text[:500]}"


def _embeddings_from_response(response_payload: object) -> list[list[float]]:
    if not isinstance(response_payload, dict):
        raise RuntimeError("Code embedding API returned an invalid response")

    data = response_payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Code embedding API response has no data array")

    indexed_embeddings: list[tuple[int, object]] = []
    for default_index, item in enumerate(data):
        if not isinstance(item, dict):
            raise RuntimeError("Code embedding API returned an invalid item")

        index = item.get("index", default_index)
        if not isinstance(index, int):
            raise RuntimeError("Code embedding API returned an invalid index")

        indexed_embeddings.append((index, item.get("embedding")))

    return [
        embedding
        for _index, embedding in sorted(
            indexed_embeddings,
            key=lambda indexed_embedding: indexed_embedding[0],
        )
        if isinstance(embedding, list)
    ]


def _vector_results_from_query_payload(
    documents: list[object],
    metadatas: list[object],
    distances: list[object],
) -> list[CodeVectorSearchResult]:
    results: list[CodeVectorSearchResult] = []
    for content, metadata, distance in zip(
        documents,
        metadatas,
        distances,
        strict=True,
    ):
        if not isinstance(metadata, Mapping):
            metadata = {}
        results.append(
            CodeVectorSearchResult(
                content=str(content),
                metadata=dict(metadata),
                score=max(0.0, 1.0 - float(str(distance))),
            )
        )
    return results


def prepare_code_embedding_chunks(
    chunks: list[ChunkMetadataDocument],
    *,
    repository_id: str | UUID | None,
    branch: str | None,
    commit_sha: str | None,
    provider: str,
    model_name: str,
    model_version: str,
    dimension: int | None,
) -> list[ChunkMetadataDocument]:
    """Attach deterministic repo-branch cache metadata to source chunks."""

    if repository_id is None:
        return chunks

    normalized_branch = _normalize_branch(branch)
    if normalized_branch is None:
        return chunks

    repo_branch_key = build_repo_branch_key(
        repository_id=repository_id,
        branch=normalized_branch,
    )
    index_generation_key = build_index_generation_key(
        repo_branch_key=repo_branch_key,
        commit_sha=commit_sha,
    )
    occurrences: dict[tuple[str, str], int] = {}
    prepared_chunks: list[ChunkMetadataDocument] = []
    for chunk in chunks:
        content = chunk.chunk_text if isinstance(chunk.chunk_text, str) else ""
        content_hash = build_content_hash(content)
        occurrence_key = (chunk.file_path, content_hash)
        occurrence_index = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence_index + 1
        embedding_cache_id = build_embedding_cache_id(
            index_generation_key=index_generation_key,
            file_path=chunk.file_path,
            content_hash=content_hash,
            occurrence_index=occurrence_index,
            provider=provider,
            model_name=model_name,
            model_version=model_version,
            dimension=dimension,
            chunker_version=CODE_CHUNKER_VERSION,
        )
        prepared_chunks.append(
            chunk.model_copy(
                update={
                    "repository_id": repository_id,
                    "branch": normalized_branch,
                    "commit_sha": commit_sha,
                    "repo_branch_key": repo_branch_key,
                    "index_generation_key": index_generation_key,
                    "content_hash": content_hash,
                    "embedding_cache_id": embedding_cache_id,
                    "occurrence_index": occurrence_index,
                    "chunker_version": CODE_CHUNKER_VERSION,
                }
            )
        )

    return prepared_chunks


def build_repo_branch_key(*, repository_id: str | UUID, branch: str) -> str:
    """Return a stable cache scope for one repository branch."""

    return _sha256_text(f"{repository_id}\0{branch}")


def build_index_generation_key(*, repo_branch_key: str, commit_sha: str | None) -> str:
    """Return the immutable vector scope for one branch snapshot."""

    normalized_commit = (commit_sha or "").strip()
    if not normalized_commit:
        return repo_branch_key

    return _sha256_text(f"{repo_branch_key}\0{normalized_commit}")


def build_content_hash(content: str) -> str:
    """Return the content hash used to detect changed chunks."""

    return _sha256_text(content)


def build_embedding_cache_id(
    *,
    index_generation_key: str,
    file_path: str,
    content_hash: str,
    occurrence_index: int,
    provider: str,
    model_name: str,
    model_version: str,
    dimension: int | None,
    chunker_version: str,
) -> str:
    """Return the vector id for one cacheable source chunk."""

    fingerprint = "\0".join(
        [
            index_generation_key,
            file_path,
            content_hash,
            str(occurrence_index),
            provider,
            model_name,
            model_version,
            str(dimension or UNKNOWN_CODE_EMBEDDING_DIMENSION),
            chunker_version,
        ]
    )
    return _sha256_text(fingerprint)


def code_embedding_model_version(provider: str) -> str:
    """Return the cache-significant model version label for a provider."""

    _ = provider
    return CODE_EMBEDDING_MODEL_VERSION


def _chunk_id(chunk: ChunkMetadataDocument) -> str:
    if chunk.embedding_cache_id:
        return chunk.embedding_cache_id

    return f"{chunk.job_id}:{chunk.file_path}:{chunk.chunk_index}"


def _chunk_metadata(
    chunk: ChunkMetadataDocument,
) -> dict[str, str | int | float | bool]:
    metadata: dict[str, str | int | float | bool] = {
        "job_id": str(chunk.job_id),
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "function_name": chunk.function_name or "",
        "class_name": chunk.class_name or "",
        "language": chunk.language,
        "risk_area": chunk.risk_area or "general",
        "module": chunk.module or "",
        "imports": json.dumps(chunk.imports, ensure_ascii=False),
    }
    optional_values: dict[str, str | int | None] = {
        "repository_id": str(chunk.repository_id) if chunk.repository_id else None,
        "branch": chunk.branch,
        "commit_sha": chunk.commit_sha,
        "repo_branch_key": chunk.repo_branch_key,
        "index_generation_key": chunk.index_generation_key,
        "content_hash": chunk.content_hash,
        "embedding_cache_id": chunk.embedding_cache_id,
        "occurrence_index": chunk.occurrence_index,
        "chunker_version": chunk.chunker_version,
    }
    for key, value in optional_values.items():
        if value is not None:
            metadata[key] = value

    return metadata


def _common_repo_branch_key(chunks: list[ChunkMetadataDocument]) -> str | None:
    repo_branch_keys = {
        chunk.repo_branch_key for chunk in chunks if chunk.repo_branch_key
    }
    if len(repo_branch_keys) == 1:
        return next(iter(repo_branch_keys))

    return None


def _common_index_generation_key(chunks: list[ChunkMetadataDocument]) -> str | None:
    index_generation_keys = {
        chunk.index_generation_key for chunk in chunks if chunk.index_generation_key
    }
    if len(index_generation_keys) == 1:
        return next(iter(index_generation_keys))

    return None


def _count_sensitive_chunks(chunks: list[ChunkMetadataDocument]) -> int:
    return sum(1 for chunk in chunks if not _can_send_chunk_to_remote(chunk))


def _can_send_chunk_to_remote(chunk: ChunkMetadataDocument) -> bool:
    content = chunk.chunk_text if isinstance(chunk.chunk_text, str) else ""
    return not _contains_high_confidence_secret(content)


def _contains_high_confidence_secret(content: str) -> bool:
    if "PRIVATE KEY-----" in content:
        return True

    secret_patterns = (
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bghp_[A-Za-z0-9_]{30,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bsk-[A-Za-z0-9]{32,}\b",
        r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][^'\"]{24,}",
    )
    return any(re.search(pattern, content) for pattern in secret_patterns)


def _normalize_branch(branch: str | None) -> str | None:
    if branch is None:
        return None

    normalized = branch.strip()
    return normalized or None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _batched(
    chunks: list[ChunkMetadataDocument],
    batch_size: int,
) -> list[list[ChunkMetadataDocument]]:
    return [
        chunks[start : start + batch_size]
        for start in range(0, len(chunks), batch_size)
    ]


def _token_limited_batches(
    chunks: list[ChunkMetadataDocument],
    *,
    max_items: int,
    max_tokens: int,
    max_item_tokens: int,
) -> list[list[ChunkMetadataDocument]]:
    batches: list[list[ChunkMetadataDocument]] = []
    current_batch: list[ChunkMetadataDocument] = []
    current_tokens = 0
    for chunk in chunks:
        chunk_tokens = min(max(chunk.token_count, 1), max_item_tokens)
        if current_batch and (
            len(current_batch) >= max_items
            or current_tokens + chunk_tokens > max_tokens
        ):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(chunk)
        current_tokens += chunk_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _truncate_embedding_text(text: str, *, max_tokens: int) -> str:
    max_chars = max(1, max_tokens * 6)
    if len(text) > max_chars:
        text = text[:max_chars]

    tokens = re.findall(r"\S+", text)
    if len(tokens) <= max_tokens:
        return text

    return " ".join(tokens[:max_tokens])


def _query_groups(queries: list[CodeVectorQuery]) -> list[list[int]]:
    groups_by_where: dict[str, list[int]] = {}
    for index, query in enumerate(queries):
        key = json.dumps(query.where, sort_keys=True, default=str)
        groups_by_where.setdefault(key, []).append(index)

    return list(groups_by_where.values())


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _token_usage_from_response(response_payload: object) -> dict[str, int] | None:
    if not isinstance(response_payload, dict):
        return None
    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _usage_int(usage, ("input_tokens", "prompt_tokens"))
    total_tokens = _usage_int(usage, ("total_tokens",))
    if total_tokens == 0:
        total_tokens = input_tokens
    result = {
        "input_tokens": input_tokens,
        "total_tokens": total_tokens,
    }
    return result if any(result.values()) else None


def _usage_int(value: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            return item
    return 0


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
