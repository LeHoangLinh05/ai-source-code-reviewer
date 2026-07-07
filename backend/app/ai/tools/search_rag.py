"""AI tool for searching coding standards and security knowledge."""

from __future__ import annotations

from langchain_core.tools import tool

from app.ai.rag.retriever import HybridRetriever

_retriever: HybridRetriever | None = None


@tool
def search_coding_standard(
    query: str,
    language: str | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Tra cứu OWASP/coding standard/best practice liên quan (hybrid vector + keyword search). BẮT BUỘC gọi trước khi flag bất kỳ issue category=security nào."""

    results = get_retriever().search(query=query, language=language, top_k=top_k)
    return {
        "results": [
            {
                "source": result.source,
                "content": result.content,
                "vector_score": result.vector_score,
                "bm25_score": result.bm25_score,
                "final_score": result.final_score,
            }
            for result in results
        ]
    }


def get_retriever() -> HybridRetriever:
    """Return a lazy singleton retriever for tool calls."""

    global _retriever

    if _retriever is None:
        _retriever = HybridRetriever()

    return _retriever
