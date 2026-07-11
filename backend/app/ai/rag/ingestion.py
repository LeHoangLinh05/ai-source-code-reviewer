"""Knowledge base ingestion workflows for RAG documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
import re

from app.ai.rag.bm25_index import BM25Document, BM25Index
from app.ai.rag.vectorstore import ChromaVectorStore, get_vectorstore

CHUNK_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 50
_TOKEN_PATTERN = re.compile(r"\S+")


@dataclass(slots=True)
class RAGDocument:
    """Raw coding-standard document ready for ingestion."""

    source: str
    content: str
    language: str
    doc_type: str
    category: str
    extra_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class IngestedChunk:
    """One document chunk inserted into the RAG stores."""

    id: str
    content: str
    metadata: dict[str, object]


class RAGIngestionPipeline:
    """Split, metadata-tag, embed, and index knowledge-base documents."""

    def __init__(
        self,
        *,
        vectorstore: ChromaVectorStore | None = None,
        bm25_index: BM25Index | None = None,
    ) -> None:
        self.vectorstore = vectorstore or get_vectorstore()
        self.bm25_index = bm25_index or BM25Index()

    def reset(self) -> None:
        """Clear persisted vectors and the in-memory keyword index."""

        self.vectorstore.reset_collection()
        self.bm25_index.clear()

    def load_document(
        self,
        file_path: Path,
        *,
        source: str,
        language: str,
        doc_type: str,
        category: str,
    ) -> RAGDocument:
        """Load one text or Markdown document from disk."""

        return RAGDocument(
            source=source,
            content=file_path.read_text(encoding="utf-8", errors="ignore"),
            language=language,
            doc_type=doc_type,
            category=category,
        )

    def ingest_document(self, document: RAGDocument) -> list[IngestedChunk]:
        """Ingest one document into ChromaDB and BM25."""

        chunks = self._split_document(document)
        if not chunks:
            return []

        self.vectorstore.add_documents(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
        )
        self.bm25_index.add_documents(
            [
                BM25Document(
                    id=chunk.id,
                    content=chunk.content,
                    metadata=chunk.metadata,
                )
                for chunk in chunks
            ]
        )
        return chunks

    def ingest_documents(self, documents: list[RAGDocument]) -> list[IngestedChunk]:
        """Ingest multiple documents in order."""

        ingested_chunks: list[IngestedChunk] = []
        for document in documents:
            ingested_chunks.extend(self.ingest_document(document))

        return ingested_chunks

    def ingest_roadmap(self, source_path: Path | None = None) -> list[IngestedChunk]:
        """Load and ingest every roadmap requirement into the knowledge base."""

        from app.ai.roadmap.knowledge import (
            ROADMAP_SOURCE_PATH,
            load_roadmap_documents,
        )

        roadmap_documents = load_roadmap_documents(source_path or ROADMAP_SOURCE_PATH)
        return self.ingest_documents(roadmap_documents)

    def _split_document(self, document: RAGDocument) -> list[IngestedChunk]:
        tokens = _tokenize_preserving_text(document.content)
        if not tokens:
            return []

        chunks: list[IngestedChunk] = []
        start = 0
        chunk_index = 0
        ingested_at = datetime.now(UTC).isoformat()
        while start < len(tokens):
            end = min(len(tokens), start + CHUNK_TOKENS)
            content = " ".join(tokens[start:end]).strip()
            if content:
                metadata: dict[str, object] = {
                    "source": document.source,
                    "chunk_index": chunk_index,
                    "language": document.language,
                    "doc_type": document.doc_type,
                    "category": document.category,
                    "ingested_at": ingested_at,
                    **document.extra_metadata,
                }
                chunks.append(
                    IngestedChunk(
                        id=_chunk_id(document.source, chunk_index, content),
                        content=content,
                        metadata=metadata,
                    )
                )
                chunk_index += 1

            if end == len(tokens):
                break

            start = end - CHUNK_OVERLAP_TOKENS

        return chunks


def _tokenize_preserving_text(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_PATTERN.finditer(text)]


def _chunk_id(source: str, chunk_index: int, content: str) -> str:
    source_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", source.lower()).strip("-")
    digest = sha1(content.encode("utf-8")).hexdigest()[:12]
    return f"{source_slug}-{chunk_index}-{digest}"
