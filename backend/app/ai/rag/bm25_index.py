"""In-memory BM25 index for coding-standard keyword retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Protocol, Sequence

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+")


class _BM25Backend(Protocol):
    def get_scores(self, query_tokens: list[str]) -> Sequence[float]:
        """Return raw BM25 scores for every indexed document."""
        ...


@dataclass(slots=True)
class BM25Document:
    """Document chunk stored in the BM25 index."""

    id: str
    content: str
    metadata: dict[str, object]


@dataclass(slots=True)
class BM25SearchResult:
    """One normalized BM25 result."""

    id: str
    content: str
    metadata: dict[str, object]
    score: float


class BM25Index:
    """Build and search a small in-memory BM25 index."""

    def __init__(self) -> None:
        self._documents: list[BM25Document] = []
        self._tokenized_documents: list[list[str]] = []
        self._bm25_backend: _BM25Backend | None = None

    @property
    def documents(self) -> list[BM25Document]:
        """Return indexed documents."""

        return self._documents

    def clear(self) -> None:
        """Remove all indexed documents."""

        self._documents = []
        self._tokenized_documents = []
        self._bm25_backend = None

    def add_documents(self, documents: list[BM25Document]) -> None:
        """Append documents and rebuild the BM25 backend."""

        if not documents:
            return

        known_ids = {document.id for document in self._documents}
        self._documents.extend(
            document for document in documents if document.id not in known_ids
        )
        self._tokenized_documents = [
            tokenize(document.content) for document in self._documents
        ]
        self._bm25_backend = self._build_backend(self._tokenized_documents)

    def search(
        self,
        query: str,
        *,
        top_k: int,
        metadata_filters: dict[str, object] | None = None,
        language: str | None = None,
    ) -> list[BM25SearchResult]:
        """Search indexed chunks and return normalized keyword scores."""

        if not self._documents or top_k <= 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        raw_scores = self._score(query_tokens)
        max_score = max(raw_scores, default=0.0)
        scored_documents = []
        for document, raw_score in zip(self._documents, raw_scores, strict=True):
            if language and document.metadata.get("language") != language:
                continue
            if metadata_filters and not _matches_filters(
                document.metadata,
                metadata_filters,
            ):
                continue

            normalized_score = raw_score / max_score if max_score > 0 else 0.0
            scored_documents.append(
                BM25SearchResult(
                    id=document.id,
                    content=document.content,
                    metadata=document.metadata,
                    score=normalized_score,
                )
            )

        return sorted(
            scored_documents,
            key=lambda result: result.score,
            reverse=True,
        )[:top_k]

    def _build_backend(
        self,
        tokenized_documents: list[list[str]],
    ) -> _BM25Backend | None:
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]
        except ImportError:
            return None

        return BM25Okapi(tokenized_documents)

    def _score(self, query_tokens: list[str]) -> list[float]:
        if self._bm25_backend is not None:
            scores = self._bm25_backend.get_scores(query_tokens)
            normalized_scores = [max(0.0, float(score)) for score in scores]
            if max(normalized_scores, default=0.0) > 0:
                return normalized_scores

        return _fallback_bm25_scores(query_tokens, self._tokenized_documents)


def tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 using lowercase alphanumeric terms."""

    return [match.group(0).lower() for match in _TOKEN_PATTERN.finditer(text)]


def _matches_filters(
    metadata: dict[str, object],
    filters: dict[str, object],
) -> bool:
    for key, expected_value in filters.items():
        actual_value = metadata.get(key)
        if isinstance(expected_value, list):
            if actual_value not in expected_value:
                return False
            continue
        if actual_value != expected_value:
            return False

    return True


def _fallback_bm25_scores(
    query_tokens: list[str],
    tokenized_documents: list[list[str]],
) -> list[float]:
    """Small BM25 implementation used only when rank-bm25 is unavailable."""

    if not tokenized_documents:
        return []

    document_count = len(tokenized_documents)
    average_length = sum(len(document) for document in tokenized_documents) / max(
        document_count,
        1,
    )
    document_frequencies = {
        token: sum(token in document for document in tokenized_documents)
        for token in set(query_tokens)
    }

    k1 = 1.5
    b = 0.75
    scores: list[float] = []
    for document in tokenized_documents:
        document_length = len(document)
        score = 0.0
        for token in query_tokens:
            term_frequency = document.count(token)
            if term_frequency == 0:
                continue

            frequency = document_frequencies[token]
            inverse_document_frequency = math.log(
                1 + (document_count - frequency + 0.5) / (frequency + 0.5)
            )
            denominator = term_frequency + k1 * (
                1 - b + b * document_length / max(average_length, 1)
            )
            score += inverse_document_frequency * (
                term_frequency * (k1 + 1) / denominator
            )

        scores.append(score)

    return scores
