"""Runtime context shared by LangChain tools during one agent session."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_job import ReviewJob

MAX_DISTINCT_SEARCHES_PER_INVESTIGATION = 5
MAX_SOURCE_SEARCHES_PER_PASS = 24
NEAR_DUPLICATE_MATCH_THRESHOLD = 0.8


@dataclass(slots=True, frozen=True)
class CodeSearchAttempt:
    """One distinct source search performed for an investigation."""

    query_id: str
    fingerprint: str
    normalized_query: str
    mode: str
    result_chunk_keys: frozenset[tuple[str, int]]


@dataclass(slots=True, frozen=True)
class SearchDecision:
    """Decision made before a source retriever is invoked."""

    status: str
    query_id: str
    normalized_query: str
    fingerprint: str
    previous_query_id: str | None = None


@dataclass(slots=True)
class AIToolRuntime:
    """Infrastructure handles hidden from public tool schemas."""

    job_id: UUID
    session_id: UUID
    sandbox_path: Path
    postgres_session: AsyncSession
    mongodb_database: AsyncIOMotorDatabase
    delivered_chunk_hashes: dict[tuple[str, int], str] = field(default_factory=dict)
    search_attempts: dict[str, list[CodeSearchAttempt]] = field(default_factory=dict)
    knowledge_search_fingerprints: dict[str, str] = field(default_factory=dict)
    source_search_count: int = 0
    rejected_issue_confidence: dict[str, float] = field(default_factory=dict)
    code_corpus: Any | None = None
    code_retriever: Any | None = None

    def register_chunk_content(
        self,
        *,
        file_path: str,
        chunk_index: int,
        content: str,
    ) -> tuple[bool, str, int]:
        """Track full chunks already delivered during this model session."""

        content_bytes = content.encode("utf-8")
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        chunk_key = (file_path, chunk_index)
        if self.delivered_chunk_hashes.get(chunk_key) == content_sha256:
            return False, content_sha256, len(content_bytes)

        self.delivered_chunk_hashes[chunk_key] = content_sha256
        return True, content_sha256, len(content_bytes)

    def prepare_code_search(
        self,
        *,
        investigation_id: str,
        query: str,
        mode: str,
        filters: tuple[str | None, ...],
    ) -> SearchDecision:
        """Reject repeated or over-budget searches before retrieval."""

        normalized_query = _normalize_query(query)
        fingerprint_source = "|".join(
            [
                investigation_id,
                mode,
                normalized_query,
                *(value or "" for value in filters),
            ]
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        query_id = fingerprint[:16]
        attempts = self.search_attempts.get(investigation_id, [])
        duplicate = next(
            (attempt for attempt in attempts if attempt.fingerprint == fingerprint),
            None,
        )
        if duplicate is not None:
            return SearchDecision(
                status="duplicate_query",
                query_id=query_id,
                normalized_query=normalized_query,
                fingerprint=fingerprint,
                previous_query_id=duplicate.query_id,
            )
        if len(attempts) >= MAX_DISTINCT_SEARCHES_PER_INVESTIGATION:
            return SearchDecision(
                status="budget_exhausted",
                query_id=query_id,
                normalized_query=normalized_query,
                fingerprint=fingerprint,
            )
        if self.source_search_count >= MAX_SOURCE_SEARCHES_PER_PASS:
            return SearchDecision(
                status="budget_exhausted",
                query_id=query_id,
                normalized_query=normalized_query,
                fingerprint=fingerprint,
            )

        return SearchDecision(
            status="ready",
            query_id=query_id,
            normalized_query=normalized_query,
            fingerprint=fingerprint,
        )

    def record_code_search(
        self,
        *,
        investigation_id: str,
        decision: SearchDecision,
        mode: str,
        result_chunk_keys: set[tuple[str, int]],
    ) -> CodeSearchAttempt | None:
        """Record a search and return a near-duplicate attempt."""

        attempts = self.search_attempts.setdefault(investigation_id, [])
        near_duplicate = next(
            (
                attempt
                for attempt in attempts
                if attempt.mode == mode
                and _query_similarity(
                    decision.normalized_query,
                    attempt.normalized_query,
                )
                >= NEAR_DUPLICATE_MATCH_THRESHOLD
                and _result_overlap(result_chunk_keys, attempt.result_chunk_keys)
                >= NEAR_DUPLICATE_MATCH_THRESHOLD
            ),
            None,
        )
        attempt = CodeSearchAttempt(
            query_id=decision.query_id,
            fingerprint=decision.fingerprint,
            normalized_query=decision.normalized_query,
            mode=mode,
            result_chunk_keys=frozenset(result_chunk_keys),
        )
        attempts.append(attempt)
        self.source_search_count += 1
        return near_duplicate

    def prepare_knowledge_search(
        self,
        *,
        query: str,
        filters: dict[str, object],
    ) -> SearchDecision:
        """Reject repeated knowledge lookups before retrieval."""

        normalized_query = _normalize_query(query)
        fingerprint_source = json_fingerprint(
            {
                "query": normalized_query,
                "filters": filters,
            }
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        query_id = fingerprint[:16]
        previous_query_id = self.knowledge_search_fingerprints.get(fingerprint)
        if previous_query_id is not None:
            return SearchDecision(
                status="duplicate_query",
                query_id=query_id,
                normalized_query=normalized_query,
                fingerprint=fingerprint,
                previous_query_id=previous_query_id,
            )

        return SearchDecision(
            status="ready",
            query_id=query_id,
            normalized_query=normalized_query,
            fingerprint=fingerprint,
        )

    def record_knowledge_search(self, decision: SearchDecision) -> None:
        """Record a successful knowledge lookup fingerprint."""

        self.knowledge_search_fingerprints[decision.fingerprint] = decision.query_id

    def investigation_search_modes(self, investigation_id: str) -> set[str]:
        """Return retrieval modes already used by one investigation."""

        return {
            attempt.mode for attempt in self.search_attempts.get(investigation_id, [])
        }

    def confidence_increase_without_new_evidence(
        self,
        *,
        fingerprint: str,
        confidence: float,
    ) -> bool:
        """Prevent confidence-only retries from crossing the persistence gate."""

        previous = self.rejected_issue_confidence.get(fingerprint)
        self.rejected_issue_confidence[fingerprint] = max(previous or 0.0, confidence)
        return previous is not None and confidence > previous


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


def json_fingerprint(payload: dict[str, object]) -> str:
    """Return deterministic JSON for search fingerprinting."""

    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _query_similarity(left: str, right: str) -> float:
    left_terms = set(re.findall(r"[a-z0-9_/.:-]+", left))
    right_terms = set(re.findall(r"[a-z0-9_/.:-]+", right))
    union = left_terms | right_terms
    if not union:
        return 1.0
    return len(left_terms & right_terms) / len(union)


def _result_overlap(
    current: set[tuple[str, int]],
    previous: frozenset[tuple[str, int]],
) -> float:
    union = current | set(previous)
    if not union:
        return 1.0
    return len(current & set(previous)) / len(union)


_runtime: ContextVar[AIToolRuntime | None] = ContextVar(
    "ai_tool_runtime",
    default=None,
)


def get_ai_tool_runtime() -> AIToolRuntime:
    """Return the runtime context for the current AI tool call."""

    runtime = _runtime.get()
    if runtime is None:
        raise RuntimeError("AI tool runtime has not been configured")

    return runtime


async def ensure_ai_job_active() -> None:
    """Stop tool execution if the backing review job was canceled/deleted."""

    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.id).where(ReviewJob.id == runtime.job_id)
    )
    if result.scalar_one_or_none() is None:
        raise RuntimeError("Review job was canceled")


@contextmanager
def ai_tool_runtime(runtime: AIToolRuntime):
    """Bind runtime context for all tool calls in one agent execution."""

    token = _runtime.set(runtime)
    try:
        yield runtime
    finally:
        _runtime.reset(token)
