"""RAG package for vector storage, ingestion, and retrieval."""

from app.ai.rag.bm25_index import BM25Document, BM25Index, BM25SearchResult
from app.ai.rag.ingestion import RAGDocument, RAGIngestionPipeline
from app.ai.rag.retriever import HybridRetriever, RetrievedChunk
from app.ai.rag.vectorstore import (
    KNOWLEDGE_EMBEDDING_MODEL,
    ChromaVectorStore,
    VectorSearchResult,
)

__all__ = [
    "KNOWLEDGE_EMBEDDING_MODEL",
    "BM25Document",
    "BM25Index",
    "BM25SearchResult",
    "ChromaVectorStore",
    "HybridRetriever",
    "RAGDocument",
    "RAGIngestionPipeline",
    "RetrievedChunk",
    "VectorSearchResult",
]
