"""Hybrid retrieval for the unified knowledge base."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.rag.bm25_index import BM25Document, BM25Index, BM25SearchResult
from app.ai.rag.vectorstore import (
    ChromaVectorStore,
    VectorSearchResult,
    get_vectorstore,
)


@dataclass(slots=True)
class RetrievedChunk:
    """One knowledge-base chunk returned to the AI agent."""

    id: str
    source: str
    content: str
    metadata: dict[str, object]
    vector_score: float
    bm25_score: float
    final_score: float


class HybridRetriever:
    """Combine ChromaDB vector search and BM25 keyword search."""

    VECTOR_WEIGHT = 0.7
    BM25_WEIGHT = 0.3
    MAX_TOP_K = 5

    def __init__(
        self,
        *,
        vectorstore: ChromaVectorStore | None = None,
        bm25_index: BM25Index | None = None,
    ) -> None:
        self.vectorstore = vectorstore or get_vectorstore()
        self.bm25_index = bm25_index or BM25Index()
        if not self.bm25_index.documents:
            self._rebuild_bm25_from_vectorstore()

    def search(
        self,
        query: str,
        *,
        doc_type: str | list[str] | None = None,
        category: str | None = None,
        language: str | None = None,
        profile_id: str | None = None,
        weeks: list[int] | None = None,
        priority: str | None = None,
        rule_id: str | None = None,
        top_k: int = 3,
    ) -> list[RetrievedChunk]:
        """Search knowledge using vector, keyword, and metadata retrieval."""

        requested_top_k = min(max(top_k, 1), self.MAX_TOP_K)
        metadata_filters = _metadata_filters(
            doc_type=doc_type,
            category=category,
            language=language,
            profile_id=profile_id,
            weeks=weeks,
            priority=priority,
            rule_id=rule_id,
        )
        where = _chroma_where(metadata_filters)
        vector_results = self.vectorstore.query(
            query=query,
            n_results=requested_top_k * 2,
            where=where,
        )
        bm25_results = self.bm25_index.search(
            query,
            top_k=requested_top_k * 2,
            metadata_filters=metadata_filters or None,
        )
        return self._merge_and_rerank(
            vector_results=vector_results,
            bm25_results=bm25_results,
            top_k=requested_top_k,
        )

    def _merge_and_rerank(
        self,
        *,
        vector_results: list[VectorSearchResult],
        bm25_results: list[BM25SearchResult],
        top_k: int,
    ) -> list[RetrievedChunk]:
        merged: dict[str, RetrievedChunk] = {}

        for result in vector_results:
            merged[result.id] = RetrievedChunk(
                id=result.id,
                source=str(result.metadata.get("source", "")),
                content=result.content,
                metadata=result.metadata,
                vector_score=result.score,
                bm25_score=0.0,
                final_score=0.0,
            )

        for bm25_result in bm25_results:
            result_id = bm25_result.id
            if result_id not in merged:
                metadata = bm25_result.metadata
                merged[result_id] = RetrievedChunk(
                    id=result_id,
                    source=str(metadata.get("source", "")),
                    content=bm25_result.content,
                    metadata=metadata,
                    vector_score=0.0,
                    bm25_score=0.0,
                    final_score=0.0,
                )

            merged[result_id].bm25_score = bm25_result.score

        for retrieved_chunk in merged.values():
            retrieved_chunk.final_score = _weighted_score(
                vector_score=retrieved_chunk.vector_score,
                bm25_score=retrieved_chunk.bm25_score,
                vector_weight=self.VECTOR_WEIGHT,
                bm25_weight=self.BM25_WEIGHT,
            )

        return sorted(
            merged.values(),
            key=lambda result: result.final_score,
            reverse=True,
        )[:top_k]

    def _rebuild_bm25_from_vectorstore(self) -> None:
        documents = [
            BM25Document(
                id=result.id,
                content=result.content,
                metadata=result.metadata,
            )
            for result in self.vectorstore.all_documents()
        ]
        self.bm25_index.add_documents(documents)


def _metadata_filters(
    *,
    doc_type: str | list[str] | None,
    category: str | None,
    language: str | None,
    profile_id: str | None,
    weeks: list[int] | None,
    priority: str | None,
    rule_id: str | None,
) -> dict[str, object]:
    filters: dict[str, object] = {}
    if doc_type:
        filters["doc_type"] = doc_type
    if category:
        filters["category"] = category
    if language:
        filters["language"] = language
    if profile_id:
        filters["profile_id"] = profile_id
    if weeks:
        filters["week"] = weeks
    if priority:
        filters["priority"] = priority
    if rule_id:
        filters["rule_id"] = rule_id

    return filters


def _chroma_where(
    metadata_filters: dict[str, object],
) -> dict[str, object] | None:
    conditions = [
        {
            key: {"$in": value} if isinstance(value, list) else value,
        }
        for key, value in metadata_filters.items()
    ]
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


def _weighted_score(
    *,
    vector_score: float,
    bm25_score: float,
    vector_weight: float,
    bm25_weight: float,
) -> float:
    """Combine available signals without penalizing single-retriever candidates."""

    weighted_total = 0.0
    available_weight = 0.0
    if vector_score > 0:
        weighted_total += vector_weight * vector_score
        available_weight += vector_weight
    if bm25_score > 0:
        weighted_total += bm25_weight * bm25_score
        available_weight += bm25_weight

    return weighted_total / available_weight if available_weight else 0.0
