"""Job-isolated semantic retrieval for repository source code."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol
from uuid import UUID

from app.ai.rag.code_embedding import (
    CodeEmbeddingStore,
    CodeVectorSearchResult,
)


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


class CodeSemanticRetriever:
    """Retrieve source chunks without ever searching outside the current job."""

    MAX_TOP_K = 10
    MAX_CANDIDATES = 100
    CANDIDATE_MULTIPLIER = 20

    def __init__(self, vectorstore: CodeVectorStore | None = None) -> None:
        self.vectorstore = vectorstore or CodeEmbeddingStore()
        # Chunk keys already served to the Review Agent, tracked per job_id so
        # a review session cannot keep re-winning the same one or two files
        # call after call. Cleared implicitly per job because keys are
        # job-scoped; call clear_job(job_id) if a job is retried in-process.
        self._served_chunk_keys: dict[str, set[tuple[str, int]]] = {}

    def search(
        self,
        *,
        query: str,
        job_id: str | UUID,
        top_k: int = 3,
        language: str | None = None,
        risk_area: str | None = None,
    ) -> list[RetrievedCodeChunk]:
        """Search code with a mandatory job filter and optional metadata filters."""

        normalized_job_id = _require_job_id(job_id)
        requested_top_k = min(max(top_k, 1), self.MAX_TOP_K)
        where = _build_where_filter(
            job_id=normalized_job_id,
            language=language,
            risk_area=risk_area,
        )
        candidate_count = min(
            self.MAX_CANDIDATES,
            max(requested_top_k * self.CANDIDATE_MULTIPLIER, requested_top_k),
        )
        results = self.vectorstore.query(
            query=query,
            n_results=candidate_count,
            where=where,
        )
        job_results = [
            RetrievedCodeChunk(
                content=result.content,
                metadata=result.metadata,
                semantic_score=result.score,
            )
            for result in results
            if result.metadata.get("job_id") == normalized_job_id
        ]
        ranked = sorted(
            job_results,
            key=lambda result: _ranking_score(result=result, query=query),
            reverse=True,
        )
        served = self._served_chunk_keys.setdefault(normalized_job_id, set())
        max_per_file = max(1, requested_top_k // 2)
        selected = _select_diverse_unseen(
            ranked,
            requested_top_k=requested_top_k,
            served=served,
            max_per_file=max_per_file,
        )
        served.update(_chunk_key(chunk) for chunk in selected)
        return selected

    def clear_job(self, job_id: str | UUID) -> None:
        """Drop served-chunk memory for a job (e.g. on retry)."""

        self._served_chunk_keys.pop(_require_job_id(job_id), None)


def _chunk_key(chunk: RetrievedCodeChunk) -> tuple[str, int]:
    chunk_index = chunk.metadata.get("chunk_index")
    return (
        str(chunk.metadata.get("file_path") or ""),
        chunk_index if isinstance(chunk_index, int) else -1,
    )


def _select_diverse_unseen(
    ranked: list[RetrievedCodeChunk],
    *,
    requested_top_k: int,
    served: set[tuple[str, int]],
    max_per_file: int,
) -> list[RetrievedCodeChunk]:
    """Pick top_k chunks favoring unseen chunks and per-file diversity.

    Three passes over the already-ranked candidates:
    1. unseen chunks, respecting the per-file cap;
    2. unseen chunks that exceeded the per-file cap (backfill);
    3. previously served chunks, only if the candidate pool is exhausted.
    Falling through to pass 3 is itself a useful signal: it means this job's
    candidate pool (not just the ranking) is too small to keep exploring.
    """

    selected: list[RetrievedCodeChunk] = []
    selected_keys: set[tuple[str, int]] = set()
    per_file_count: dict[str, int] = {}
    deferred: list[RetrievedCodeChunk] = []

    for chunk in ranked:
        if len(selected) >= requested_top_k:
            return selected
        key = _chunk_key(chunk)
        if key in served:
            continue
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

    if len(selected) < requested_top_k:
        for chunk in ranked:
            if len(selected) >= requested_top_k:
                break
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


def _build_where_filter(
    *,
    job_id: str,
    language: str | None,
    risk_area: str | None,
) -> dict[str, object]:
    filters: list[dict[str, object]] = [{"job_id": job_id}]
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
