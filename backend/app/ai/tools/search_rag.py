"""AI tool for searching coding standards and security knowledge."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from app.ai.rag.retriever import HybridRetriever
from app.ai.tools.common import parse_json_object_text, unwrap_react_json_input

_retriever: HybridRetriever | None = None


class SearchCodingStandardInput(BaseModel):
    """Input schema for coding standard retrieval."""

    query: str
    language: str | None = None
    top_k: int = 3

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        return unwrap_react_json_input(data, "query")


@tool(args_schema=SearchCodingStandardInput)
def search_coding_standard(
    query: str,
    language: str | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Tra cứu OWASP/coding standard/best practice liên quan (hybrid vector + keyword search). BẮT BUỘC gọi trước khi flag bất kỳ issue category=security nào."""

    parsed_input = parse_json_object_text(query)
    if parsed_input is not None:
        query = str(parsed_input.get("query", query))
        language_value = parsed_input.get("language")
        if isinstance(language_value, str):
            language = language_value
        top_k_value = parsed_input.get("top_k")
        if isinstance(top_k_value, int):
            top_k = top_k_value

    results = get_retriever().search(query=query, language=language, top_k=top_k)
    return {
        "status": "ok",
        "results": [
            {
                "source": result.source,
                "content": result.content,
                "vector_score": result.vector_score,
                "bm25_score": result.bm25_score,
                "final_score": result.final_score,
            }
            for result in results
        ],
    }


def get_retriever() -> HybridRetriever:
    """Return a lazy singleton retriever for tool calls."""

    global _retriever

    if _retriever is None:
        _retriever = HybridRetriever()

    return _retriever
