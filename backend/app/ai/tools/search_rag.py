"""Backward-compatible imports for the renamed knowledge search tool."""

from app.ai.tools.search_knowledge import (
    SearchCodingStandardInput,
    SearchKnowledgeBaseInput,
    get_retriever,
    search_coding_standard,
    search_knowledge_base,
)

__all__ = [
    "SearchCodingStandardInput",
    "SearchKnowledgeBaseInput",
    "get_retriever",
    "search_coding_standard",
    "search_knowledge_base",
]
