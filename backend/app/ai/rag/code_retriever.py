"""Job-isolated semantic retrieval for repository source code."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol
from uuid import UUID

from app.ai.rag.code_embedding import (
    CodeEmbeddingStore,
    CodeVectorQuery,
    CodeVectorSearchResult,
)
from app.core.config import get_settings


class CodeVectorStore(Protocol):
    """Minimal vector-store contract required by the code retriever."""

    def query(
        self,
        *,
        query: str,
        n_results: int,
        where: dict[str, object],
    ) -> list[CodeVectorSearchResult]: ...


@dataclass(slots=True, frozen=True)
class RetrievedCodeChunk:
    """Full source chunk and metadata returned by semantic retrieval."""

    content: str
    metadata: dict[str, object]
    semantic_score: float


@dataclass(slots=True, frozen=True)
class CodeSemanticSearchRequest:
    """One scoped semantic code retrieval request."""

    query: str
    job_id: str | UUID
    repo_branch_key: str | None = None
    index_generation_key: str | None = None
    top_k: int = 3
    language: str | None = None
    risk_area: str | None = None


class CodeSemanticRetriever:
    """Retrieve source chunks without ever searching outside the current job."""

    MAX_TOP_K = 10
    MAX_CANDIDATES = 100
    CANDIDATE_MULTIPLIER = 20

    def __init__(self, vectorstore: CodeVectorStore | None = None) -> None:
        self.vectorstore = vectorstore or CodeEmbeddingStore()

    def search(
        self,
        *,
        query: str,
        job_id: str | UUID,
        repo_branch_key: str | None = None,
        index_generation_key: str | None = None,
        top_k: int = 3,
        language: str | None = None,
        risk_area: str | None = None,
    ) -> list[RetrievedCodeChunk]:
        """Search code with a mandatory repository snapshot or job fallback."""

        return self.search_many(
            [
                CodeSemanticSearchRequest(
                    query=query,
                    job_id=job_id,
                    repo_branch_key=repo_branch_key,
                    index_generation_key=index_generation_key,
                    top_k=top_k,
                    language=language,
                    risk_area=risk_area,
                )
            ]
        )[0]

    def search_many(
        self,
        requests: list[CodeSemanticSearchRequest],
    ) -> list[list[RetrievedCodeChunk]]:
        """Search many scoped queries while deduping exact duplicates."""

        if not requests:
            return []

        settings = get_settings()
        output: list[list[RetrievedCodeChunk]] = [[] for _request in requests]
        vector_queries: list[CodeVectorQuery] = []
        vector_request_indexes: list[int] = []
        duplicate_indexes: dict[int, list[int]] = {}
        seen_keys: dict[str, int] = {}
        prepared: dict[
            int,
            tuple[str, str | None, str | None, int, dict[str, object]],
        ] = {}
        for index, request in enumerate(requests):
            normalized_query = request.query.strip()
            if not normalized_query:
                continue
            if (
                _estimated_tokens(normalized_query)
                > settings.probe_semantic_max_query_tokens
            ):
                continue

            normalized_job_id = _require_job_id(request.job_id)
            normalized_repo_branch_key = _normalize_repo_branch_key(
                request.repo_branch_key
            )
            normalized_index_generation_key = _normalize_repo_branch_key(
                request.index_generation_key
            )
            requested_top_k = min(max(request.top_k, 1), self.MAX_TOP_K)
            where = _build_where_filter(
                job_id=normalized_job_id,
                repo_branch_key=normalized_repo_branch_key,
                index_generation_key=normalized_index_generation_key,
                language=request.language,
                risk_area=request.risk_area,
            )
            dedupe_key = _search_request_key(
                query=normalized_query,
                job_id=normalized_job_id,
                repo_branch_key=normalized_repo_branch_key,
                index_generation_key=normalized_index_generation_key,
                top_k=requested_top_k,
                language=request.language,
                risk_area=request.risk_area,
            )
            original_index = seen_keys.get(dedupe_key)
            if original_index is not None:
                duplicate_indexes.setdefault(original_index, []).append(index)
                continue

            seen_keys[dedupe_key] = index
            candidate_count = min(
                self.MAX_CANDIDATES,
                max(requested_top_k * self.CANDIDATE_MULTIPLIER, requested_top_k),
            )
            prepared[index] = (
                normalized_job_id,
                normalized_repo_branch_key,
                normalized_index_generation_key,
                requested_top_k,
                where,
            )
            vector_request_indexes.append(index)
            vector_queries.append(
                CodeVectorQuery(
                    query=normalized_query,
                    n_results=candidate_count,
                    where=where,
                )
            )

        for batch_start in range(
            0,
            len(vector_queries),
            settings.probe_semantic_query_batch_size,
        ):
            query_batch = vector_queries[
                batch_start : batch_start + settings.probe_semantic_query_batch_size
            ]
            request_indexes = vector_request_indexes[
                batch_start : batch_start + settings.probe_semantic_query_batch_size
            ]
            query_many = getattr(self.vectorstore, "query_many", None)
            batch_results = (
                query_many(query_batch)
                if callable(query_many)
                else [
                    self.vectorstore.query(
                        query=query.query,
                        n_results=query.n_results,
                        where=query.where,
                    )
                    for query in query_batch
                ]
            )
            for request_index, results in zip(
                request_indexes,
                batch_results,
                strict=True,
            ):
                (
                    normalized_job_id,
                    normalized_repo_branch_key,
                    normalized_index_generation_key,
                    requested_top_k,
                    _where,
                ) = prepared[request_index]
                output[request_index] = self._rank_results(
                    results=results,
                    query=requests[request_index].query,
                    job_id=normalized_job_id,
                    repo_branch_key=normalized_repo_branch_key,
                    index_generation_key=normalized_index_generation_key,
                    requested_top_k=requested_top_k,
                )

        for original_index, indexes in duplicate_indexes.items():
            for index in indexes:
                output[index] = list(output[original_index])

        return output

    def _rank_results(
        self,
        *,
        results: list[CodeVectorSearchResult],
        query: str,
        job_id: str,
        repo_branch_key: str | None,
        index_generation_key: str | None,
        requested_top_k: int,
    ) -> list[RetrievedCodeChunk]:
        job_results = [
            RetrievedCodeChunk(
                content=result.content,
                metadata=result.metadata,
                semantic_score=result.score,
            )
            for result in results
            if _metadata_matches_scope(
                result.metadata,
                job_id=job_id,
                repo_branch_key=repo_branch_key,
                index_generation_key=index_generation_key,
            )
        ]
        ranked = sorted(
            job_results,
            key=lambda result: _ranking_score(result=result, query=query),
            reverse=True,
        )
        max_per_file = max(1, requested_top_k // 2)
        return _select_diverse(
            ranked,
            requested_top_k=requested_top_k,
            max_per_file=max_per_file,
        )


def _chunk_key(chunk: RetrievedCodeChunk) -> tuple[str, int]:
    chunk_index = chunk.metadata.get("chunk_index")
    return (
        str(chunk.metadata.get("file_path") or ""),
        chunk_index if isinstance(chunk_index, int) else -1,
    )


def _select_diverse(
    ranked: list[RetrievedCodeChunk],
    *,
    requested_top_k: int,
    max_per_file: int,
) -> list[RetrievedCodeChunk]:
    """Pick a stable, path-diverse top-k from already-ranked candidates."""

    selected: list[RetrievedCodeChunk] = []
    selected_keys: set[tuple[str, int]] = set()
    per_file_count: dict[str, int] = {}
    deferred: list[RetrievedCodeChunk] = []

    for chunk in ranked:
        if len(selected) >= requested_top_k:
            return selected
        key = _chunk_key(chunk)
        file_path = str(chunk.metadata.get("file_path") or "")
        if per_file_count.get(file_path, 0) >= max_per_file:
            deferred.append(chunk)
            continue
        selected.append(chunk)
        selected_keys.add(key)
        per_file_count[file_path] = per_file_count.get(file_path, 0) + 1

    for chunk in deferred:
        if len(selected) >= requested_top_k:
            return selected
        key = _chunk_key(chunk)
        if key in selected_keys:
            continue
        selected.append(chunk)
        selected_keys.add(key)

    return selected


def _require_job_id(job_id: str | UUID) -> str:
    if isinstance(job_id, UUID):
        return str(job_id)

    normalized_job_id = str(job_id).strip() if job_id is not None else ""
    if not normalized_job_id:
        raise ValueError("job_id is required for code semantic search")

    return normalized_job_id


def _normalize_repo_branch_key(repo_branch_key: str | None) -> str | None:
    if repo_branch_key is None:
        return None

    normalized = repo_branch_key.strip()
    return normalized or None


def _estimated_tokens(text: str) -> int:
    return max(1, len(re.findall(r"\S+", text)))


def _search_request_key(
    *,
    query: str,
    job_id: str,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    top_k: int,
    language: str | None,
    risk_area: str | None,
) -> str:
    return "\0".join(
        [
            " ".join(query.lower().split()),
            job_id,
            index_generation_key or "",
            repo_branch_key or "",
            str(top_k),
            language or "",
            risk_area or "",
        ]
    )


def _metadata_matches_scope(
    metadata: dict[str, object],
    *,
    job_id: str,
    repo_branch_key: str | None,
    index_generation_key: str | None,
) -> bool:
    if index_generation_key is not None:
        return metadata.get("index_generation_key") == index_generation_key
    if repo_branch_key is not None:
        return metadata.get("repo_branch_key") == repo_branch_key

    return metadata.get("job_id") == job_id


def _build_where_filter(
    *,
    job_id: str,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    language: str | None,
    risk_area: str | None,
) -> dict[str, object]:
    filters: list[dict[str, object]] = [
        (
            {"index_generation_key": index_generation_key}
            if index_generation_key
            else {"repo_branch_key": repo_branch_key}
            if repo_branch_key
            else {"job_id": job_id}
        )
    ]
    if language:
        filters.append({"language": language})
    if risk_area:
        filters.append({"risk_area": risk_area})

    if len(filters) == 1:
        return filters[0]

    return {"$and": filters}


def _ranking_score(*, result: RetrievedCodeChunk, query: str) -> float:
    file_path = str(result.metadata.get("file_path") or "")
    # file_path is deliberately excluded from the lexical searchable text.
    # _source_path_score already accounts for directory/extension signal;
    # matching query terms against the raw filename too let generic names
    # like auth.py/main.py win almost any query that merely mentions a broad
    # domain word (e.g. "authentication"), regardless of whether that specific
    # chunk's content was actually relevant.
    searchable_text = " ".join(
        [
            str(result.metadata.get("module") or ""),
            str(result.metadata.get("function_name") or ""),
            str(result.metadata.get("class_name") or ""),
            result.content[:4_000],
        ]
    ).lower()

    return (
        result.semantic_score
        + _lexical_score(query=query, searchable_text=searchable_text)
        + _source_path_score(file_path)
    )


def _lexical_score(*, query: str, searchable_text: str) -> float:
    query_terms = _query_terms(query)
    if not query_terms:
        return 0.0

    matches = sum(1 for term in query_terms if term in searchable_text)
    return min(matches / max(len(query_terms), 1), 1.0) * 0.35


def _source_path_score(file_path: str) -> float:
    normalized_path = file_path.replace("\\", "/").lower()
    path_parts = set(normalized_path.split("/"))
    filename = normalized_path.rsplit("/", 1)[-1]

    score = 0.0
    if path_parts & {
        "app",
        "api",
        "backend",
        "frontend",
        "src",
        "services",
        "routers",
        "repositories",
    }:
        score += 0.2
    if filename.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
        score += 0.15
    if _is_non_runtime_evidence_path(normalized_path):
        score -= 0.75

    return score


def _is_non_runtime_evidence_path(normalized_path: str) -> bool:
    filename = normalized_path.rsplit("/", 1)[-1]
    return (
        filename.endswith((".md", ".txt"))
        or "test_spec" in filename
        or "verify_logic" in filename
        or filename.startswith("test_")
        or "/tests/" in normalized_path
        or "/test/" in normalized_path
        or "__tests__" in normalized_path
    )


def _query_terms(query: str) -> set[str]:
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_/.:-]+", query)
        if len(token) >= 3
    }
    return {
        term
        for term in terms
        if term
        not in {
            "and",
            "for",
            "the",
            "this",
            "that",
            "with",
            "rule",
            "whether",
            "repository",
            "requirement",
            "verification",
            "hint",
            "inspect",
            "evidence",
            "related",
            "look",
            "real",
            "control",
            "data",
            "flow",
        }
    }
