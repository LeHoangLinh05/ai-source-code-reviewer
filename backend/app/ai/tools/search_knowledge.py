"""AI tools for hybrid search across the unified knowledge base."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from app.ai.rag.retriever import HybridRetriever
from app.ai.tool_runtime import AIToolRuntime, get_ai_tool_runtime
from app.ai.tools.common import unwrap_react_json_input

_retriever: HybridRetriever | None = None
_CODING_KNOWLEDGE_TYPES = ["standard", "guideline", "checklist"]


class SearchKnowledgeBaseInput(BaseModel):
    """Input schema for unified knowledge retrieval."""

    query: str
    doc_type: str | None = None
    category: str | None = None
    language: str | None = None
    profile_id: str | None = None
    weeks: list[int] | None = None
    priority: str | None = None
    rule_id: str | None = None
    top_k: int = Field(default=3, ge=1)

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        return _clamp_top_k(unwrap_react_json_input(data, "query"))


class SearchCodingStandardInput(BaseModel):
    """Compatibility input schema for coding/security knowledge retrieval."""

    query: str
    language: str | None = None
    top_k: int = Field(default=3, ge=1)

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        return _clamp_top_k(unwrap_react_json_input(data, "query"))


@tool(args_schema=SearchKnowledgeBaseInput)
def search_knowledge_base(
    query: str,
    doc_type: str | None = None,
    category: str | None = None,
    language: str | None = None,
    profile_id: str | None = None,
    weeks: list[int] | None = None,
    priority: str | None = None,
    rule_id: str | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Search standards, guidelines, checklists, project docs, or roadmap rules."""

    return _search(
        query=query,
        doc_type=doc_type,
        category=category,
        language=language,
        profile_id=profile_id,
        weeks=weeks,
        priority=priority,
        rule_id=rule_id,
        top_k=top_k,
    )


@tool(args_schema=SearchCodingStandardInput)
def search_coding_standard(
    query: str,
    language: str | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Compatibility wrapper limited to coding and security knowledge."""

    return _search(
        query=query,
        doc_type=_CODING_KNOWLEDGE_TYPES,
        language=language,
        top_k=top_k,
    )


def _search(
    *,
    query: str,
    doc_type: str | list[str] | None = None,
    category: str | None = None,
    language: str | None = None,
    profile_id: str | None = None,
    weeks: list[int] | None = None,
    priority: str | None = None,
    rule_id: str | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    filters: dict[str, object] = {
        "doc_type": doc_type,
        "category": category,
        "language": language,
        "profile_id": profile_id,
        "weeks": weeks,
        "priority": priority,
        "rule_id": rule_id,
        "top_k": top_k,
    }
    runtime = _optional_runtime()
    decision = None
    if runtime is not None:
        decision = runtime.prepare_knowledge_search(query=query, filters=filters)
        if decision.status == "duplicate_query":
            return {
                "status": "duplicate_query",
                "query_id": decision.query_id,
                "previous_query_id": decision.previous_query_id,
                "normalized_query": decision.normalized_query,
                "results": [],
                "next_action": (
                    "Use the earlier knowledge_base observation for this exact "
                    "query/filter set; do not retrieve it again."
                ),
            }

    retriever = get_retriever()
    results = retriever.search(
        query=query,
        doc_type=doc_type,
        category=category,
        language=language,
        profile_id=profile_id,
        weeks=weeks,
        priority=priority,
        rule_id=rule_id,
        top_k=top_k,
    )
    if not results and rule_id is not None:
        results = retriever.search(
            query=rule_id,
            doc_type=doc_type,
            profile_id=profile_id,
            rule_id=rule_id,
            top_k=top_k,
        )
    if runtime is not None and decision is not None:
        runtime.record_knowledge_search(decision)
    return {
        "status": "ok",
        "query_id": decision.query_id if decision is not None else None,
        "normalized_query": (
            decision.normalized_query
            if decision is not None
            else " ".join(query.split())
        ),
        "results": [
            {
                "source": result.source,
                "content": result.content,
                "metadata": result.metadata,
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


def _optional_runtime() -> AIToolRuntime | None:
    try:
        return get_ai_tool_runtime()
    except RuntimeError:
        return None


def _clamp_top_k(data: object) -> object:
    if not isinstance(data, dict) or "top_k" not in data:
        return data

    try:
        top_k = int(data["top_k"])
    except (TypeError, ValueError):
        return data

    normalized = dict(data)
    normalized["top_k"] = min(max(top_k, 1), HybridRetriever.MAX_TOP_K)
    return normalized
