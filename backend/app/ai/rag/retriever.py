"""Hybrid retrieval for coding standard lookup."""

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
    """One coding-standard chunk returned to the AI agent."""

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
        language: str | None = None,
        top_k: int = 3,
    ) -> list[RetrievedChunk]:
        """Search coding standards using vector and keyword retrieval."""

        requested_top_k = min(max(top_k, 1), self.MAX_TOP_K)
        where = {"language": language} if language else None
        vector_results = self.vectorstore.query(
            query=query,
            n_results=requested_top_k * 2,
            where=where,
        )
        bm25_results = self.bm25_index.search(
            query,
            top_k=requested_top_k * 2,
            language=language,
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

        for result in bm25_results:
            result_id = result.id
            if result_id not in merged:
                metadata = result.metadata
                merged[result_id] = RetrievedChunk(
                    id=result_id,
                    source=str(metadata.get("source", "")),
                    content=result.content,
                    metadata=metadata,
                    vector_score=0.0,
                    bm25_score=0.0,
                    final_score=0.0,
                )

            merged[result_id].bm25_score = result.score

        for result in merged.values():
            result.final_score = (
                self.VECTOR_WEIGHT * result.vector_score
                + self.BM25_WEIGHT * result.bm25_score
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
