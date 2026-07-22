"""Backend-directed probe retrieval and evidence-only issue judging."""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.probe_contracts import ProbeDefinition, ProbeLane
from app.ai.rag.bm25_index import BM25Document, BM25Index, tokenize
from app.ai.rag.code_retriever import CodeSemanticRetriever, CodeSemanticSearchRequest
from app.ai.review_plan import REVIEW_MODE_FULL_AUDIT, get_review_mode
from app.ai.roadmap.knowledge import RoadmapRequirement, load_roadmap_requirements
from app.ai.roadmap.selection import build_roadmap_context
from app.ai.semantic_audit_plan import build_semantic_audit_plan
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
)
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.models.review_job import ReviewJob

logger = logging.getLogger(__name__)

PROBE_RETRIEVAL_TOOL_NAME = "probe_retrieval"
PROBE_JUDGE_TOOL_NAME = "probe_judge"
MIN_PROBE_ISSUE_CONFIDENCE = 0.7
MIN_IMPORTANT_QUERY_TERM_LENGTH = 3
MIN_IMPORTANT_RULE_TERM_LENGTH = 4
STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "has",
    "have",
    "into",
    "the",
    "this",
    "that",
    "with",
}
PATH_HINTS_BY_CATEGORY = {
    "security": ("auth", "security", "jwt", "token", "session", "middleware"),
    "bug": ("service", "worker", "task", "route", "api"),
    "performance": ("repository", "database", "db", "cache", "query"),
    "maintainability": ("service", "utils", "helper", "common", "core"),
    "style": ("api", "schema", "model", "config"),
    "requirement": ("app", "src", "backend", "frontend", "config"),
}
RRF_K = 60
MAX_RELATED_CHUNKS = 2
FULL_AUDIT_CHUNKS_PER_PROBE = 6
TRACE_ID_HASH_LENGTH = 12
SMART_LANE_CALL_BUDGET_WITH_ROADMAP = {
    ProbeLane.DEFECT: 4,
    ProbeLane.COVERAGE: 1,
    ProbeLane.ROADMAP: 4,
}
SMART_LANE_CALL_BUDGET_WITHOUT_ROADMAP = {
    ProbeLane.DEFECT: 8,
    ProbeLane.COVERAGE: 1,
    ProbeLane.ROADMAP: 0,
}


class SyntheticTraceWriter(Protocol):
    """Subset of MongoToolCallLogger needed by backend-directed review."""

    async def write_synthetic_tool_log(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, object],
        output: dict[str, object],
    ) -> None: ...


@dataclass(slots=True, frozen=True)
class ProbeCandidateChunk:
    """One selected source chunk for a probe evidence bundle."""

    file_path: str
    chunk_index: int
    line_start: int
    line_end: int
    language: str
    risk_area: str
    content: str
    semantic_score: float
    lexical_score: float
    path_score: float
    static_score: float
    final_score: float
    strategies: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, int]:
        return self.file_path, self.chunk_index


@dataclass(slots=True, frozen=True)
class ProbeEvidenceBundle:
    """Retrieved evidence for one semantic audit probe."""

    probe: ProbeDefinition
    retrieval_status: str
    candidate_chunks: list[ProbeCandidateChunk]
    strategies_used: list[str]
    strategy_candidate_counts: dict[str, int]
    selected_count_before_trim: int
    trimmed_count: int = 0


@dataclass(slots=True, frozen=True)
class ProbeReviewResult:
    """Summary returned to the AI pipeline after probe review."""

    total_probes: int
    retrieved_probes: int
    no_evidence_probes: int
    judged_batches: int
    created_issues: int
    rejected_candidates: int
    handoff: str


class ProbeJudgeEvidenceReference(BaseModel):
    """Chunk evidence reference returned by the probe judge."""

    file_path: str
    chunk_index: int
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    rationale: str | None = None


class ProbeJudgeIssueCandidate(BaseModel):
    """Structured issue candidate returned by the evidence-only judge."""

    verdict: str
    claim_type: str | None = None
    title: str | None = None
    description: str | None = None
    suggestion: str | None = None
    severity: str | None = None
    category: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    supporting_evidence: list[ProbeJudgeEvidenceReference] = Field(default_factory=list)
    contradicting_evidence: list[ProbeJudgeEvidenceReference] = Field(
        default_factory=list
    )
    rule_id: str | None = None
    probe_id: str | None = None

    @field_validator(
        "supporting_evidence",
        "contradicting_evidence",
        mode="before",
    )
    @classmethod
    def _normalize_evidence_references(cls, value: object) -> list[object]:
        if value is None or isinstance(value, str):
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return value
        return []


class ProbeJudgeResponse(BaseModel):
    """Root JSON object expected from the LLM judge."""

    candidates: list[ProbeJudgeIssueCandidate] = Field(default_factory=list)
    schema_rejected_count: int = 0


class ProbeRetrievalService:
    """Retrieve source evidence for every category probe without LLM calls."""

    def __init__(
        self,
        *,
        database: AsyncIOMotorDatabase,
        code_retriever: CodeSemanticRetriever | None = None,
        enable_semantic_search: bool = True,
        chunks_per_probe: int = 3,
        max_chunks: int = 188,
        defect_max_chunks: int = 120,
        coverage_max_chunks: int = 24,
        roadmap_max_chunks: int = 44,
        max_probes_per_batch: int = 8,
        max_chunks_per_batch: int = 24,
        full_audit: bool = False,
    ) -> None:
        self.database = database
        self.code_retriever = code_retriever
        self.enable_semantic_search = enable_semantic_search
        self.chunks_per_probe = max(1, chunks_per_probe)
        self.max_chunks = max(1, max_chunks)
        self.lane_max_chunks = {
            ProbeLane.DEFECT: max(1, defect_max_chunks),
            ProbeLane.COVERAGE: max(1, coverage_max_chunks),
            ProbeLane.ROADMAP: max(1, roadmap_max_chunks),
        }
        self.max_probes_per_batch = max(1, max_probes_per_batch)
        self.max_chunks_per_batch = max(1, max_chunks_per_batch)
        self.full_audit = full_audit

    async def retrieve(
        self,
        *,
        job_id: UUID,
        probes: list[ProbeDefinition],
        trace_writer: SyntheticTraceWriter,
    ) -> list[ProbeEvidenceBundle]:
        """Run hybrid retrieval for every probe and write redacted traces."""

        chunk_documents = await _load_chunk_documents(self.database, job_id)
        repo_branch_key = _repo_branch_key_from_documents(chunk_documents)
        index_generation_key = _index_generation_key_from_documents(chunk_documents)
        bm25_index = _build_bm25_index(chunk_documents)
        semantic_by_query = await self._semantic_results_for_probes(
            job_id=job_id,
            repo_branch_key=repo_branch_key,
            index_generation_key=index_generation_key,
            probes=probes,
        )
        bundles: list[ProbeEvidenceBundle] = []
        durations_by_probe: dict[str, int] = {}
        defect_evidence_keys: set[tuple[str, int]] = set()
        coverage_evidence_keys: set[tuple[str, int]] = set()
        has_roadmap = any(probe.lane is ProbeLane.ROADMAP for probe in probes)
        for lane in ProbeLane:
            lane_bundles: list[ProbeEvidenceBundle] = []
            for probe_index, probe in enumerate(probes):
                if probe.lane is not lane:
                    continue
                started_at = time.perf_counter()
                excluded_keys = (
                    defect_evidence_keys | coverage_evidence_keys
                    if lane is ProbeLane.COVERAGE
                    else set()
                )
                bundle = await self._retrieve_probe(
                    job_id=job_id,
                    repo_branch_key=repo_branch_key,
                    index_generation_key=index_generation_key,
                    probe=probe,
                    semantic_results=[
                        result
                        for query_index in range(len(probe.retrieval_queries))
                        for result in semantic_by_query.get(
                            (probe_index, query_index),
                            [],
                        )
                    ],
                    semantic_attempted=any(
                        (probe_index, query_index) in semantic_by_query
                        for query_index in range(len(probe.retrieval_queries))
                    ),
                    chunk_documents=chunk_documents,
                    bm25_index=bm25_index,
                    excluded_keys=excluded_keys,
                )
                lane_bundles.append(bundle)
                durations_by_probe[probe.probe_id] = int(
                    (time.perf_counter() - started_at) * 1000
                )
                if lane is ProbeLane.COVERAGE:
                    coverage_evidence_keys.update(
                        chunk.key for chunk in bundle.candidate_chunks
                    )

            lane_bundles = self._apply_smart_per_probe_cap(
                bundles=lane_bundles,
                lane=lane,
                has_roadmap=has_roadmap,
            )
            trimmed_lane_bundles = _trim_bundles(
                lane_bundles,
                max_chunks=self._lane_chunk_cap(
                    lane=lane,
                    has_roadmap=has_roadmap,
                ),
            )
            call_budget = self._lane_call_budget(
                lane=lane,
                has_roadmap=has_roadmap,
            )
            nonempty_probe_count = sum(
                bool(bundle.candidate_chunks) for bundle in trimmed_lane_bundles
            )
            if (
                not self.full_audit
                and call_budget > 0
                and nonempty_probe_count > call_budget * self.max_probes_per_batch
            ):
                logger.warning(
                    "Probe lane %s exceeds its smart judge probe budget: %s > %s",
                    lane.value,
                    nonempty_probe_count,
                    call_budget * self.max_probes_per_batch,
                )
            bundles.extend(trimmed_lane_bundles)
            if lane is ProbeLane.DEFECT:
                defect_evidence_keys.update(
                    chunk.key
                    for bundle in trimmed_lane_bundles
                    for chunk in bundle.candidate_chunks
                )

        if self.full_audit:
            trimmed_bundles = [
                *bundles,
                *_full_audit_bundles(
                    chunk_documents=chunk_documents,
                    existing_bundles=bundles,
                ),
            ]
        else:
            trimmed_bundles = _trim_bundles(bundles, max_chunks=self.max_chunks)
        for bundle in trimmed_bundles:
            await _write_probe_retrieval_trace(
                trace_writer=trace_writer,
                probe=bundle.probe,
                bundle=bundle,
                duration_ms=durations_by_probe.get(bundle.probe.probe_id, 0),
            )

        return trimmed_bundles

    def _lane_chunk_cap(self, *, lane: ProbeLane, has_roadmap: bool) -> int:
        if self.full_audit:
            return self.lane_max_chunks[lane]
        call_budget = self._lane_call_budget(
            lane=lane,
            has_roadmap=has_roadmap,
        )
        if call_budget <= 0:
            return self.lane_max_chunks[lane]
        return min(
            self.lane_max_chunks[lane],
            call_budget * self.max_chunks_per_batch,
        )

    @staticmethod
    def _lane_call_budget(*, lane: ProbeLane, has_roadmap: bool) -> int:
        return (
            SMART_LANE_CALL_BUDGET_WITH_ROADMAP
            if has_roadmap
            else SMART_LANE_CALL_BUDGET_WITHOUT_ROADMAP
        )[lane]

    def _apply_smart_per_probe_cap(
        self,
        *,
        bundles: list[ProbeEvidenceBundle],
        lane: ProbeLane,
        has_roadmap: bool,
    ) -> list[ProbeEvidenceBundle]:
        if self.full_audit or not has_roadmap or lane is not ProbeLane.DEFECT:
            return bundles
        per_probe_cap = max(
            1,
            self.max_chunks_per_batch // self.max_probes_per_batch,
        )
        return [
            replace(
                bundle,
                candidate_chunks=bundle.candidate_chunks[:per_probe_cap],
                trimmed_count=bundle.trimmed_count
                + max(0, len(bundle.candidate_chunks) - per_probe_cap),
            )
            for bundle in bundles
        ]

    async def _semantic_results_for_probes(
        self,
        *,
        job_id: UUID,
        repo_branch_key: str | None,
        index_generation_key: str | None,
        probes: list[ProbeDefinition],
    ) -> dict[tuple[int, int], list[Any]]:
        if not self.enable_semantic_search:
            return {}

        requests: list[CodeSemanticSearchRequest] = []
        request_indexes: list[tuple[int, int]] = []
        for probe_index, probe in enumerate(probes):
            for query_index, query in enumerate(probe.retrieval_queries):
                normalized_query = query.strip()
                if not normalized_query:
                    continue
                requests.append(
                    CodeSemanticSearchRequest(
                        query=normalized_query,
                        job_id=job_id,
                        repo_branch_key=repo_branch_key,
                        index_generation_key=index_generation_key,
                        top_k=10,
                    )
                )
                request_indexes.append((probe_index, query_index))

        if not requests:
            return {}

        try:
            results_by_request = await _semantic_search_many(
                retriever=self._code_retriever(),
                requests=requests,
            )
        except Exception as error:
            logger.warning("Probe semantic retrieval batch failed: %s", error)
            return {request_index: [] for request_index in request_indexes}

        return {
            request_index: results
            for request_index, results in zip(
                request_indexes,
                results_by_request,
                strict=True,
            )
        }

    async def _retrieve_probe(
        self,
        *,
        job_id: UUID,
        repo_branch_key: str | None,
        index_generation_key: str | None,
        probe: ProbeDefinition,
        semantic_results: list[Any] | None,
        semantic_attempted: bool,
        chunk_documents: list[dict[str, Any]],
        bm25_index: BM25Index,
        excluded_keys: set[tuple[str, int]],
    ) -> ProbeEvidenceBundle:
        query = " ".join(probe.retrieval_queries)
        top_k = min(max(probe.top_k, 1), 10)
        semantic_candidates: list[ProbeCandidateChunk] = []
        bm25_candidates: list[ProbeCandidateChunk] = []
        strategies: list[str] = []

        semantic_results = semantic_results or []
        if semantic_attempted:
            strategies.append("semantic")
        if query and self.enable_semantic_search and not semantic_attempted:
            try:
                semantic_results = await _semantic_search(
                    retriever=self._code_retriever(),
                    job_id=job_id,
                    repo_branch_key=repo_branch_key,
                    index_generation_key=index_generation_key,
                    query=probe.primary_query,
                    top_k=10,
                )
                strategies.append("semantic")
            except Exception as error:
                logger.warning(
                    "Probe semantic retrieval failed; using exact only: %s", error
                )
                strategies.append("semantic_error")

        for result in semantic_results:
            chunk = _candidate_from_semantic_result(result, probe=probe, query=query)
            if chunk is not None:
                semantic_candidates.append(chunk)

        for retrieval_query in probe.retrieval_queries:
            lexical_results = bm25_index.search(retrieval_query, top_k=50)
            for result in lexical_results:
                chunk = _candidate_from_document(
                    result.metadata,
                    probe=probe,
                    query=retrieval_query,
                    semantic_score=0.0,
                    lexical_score=result.score,
                    strategy="bm25",
                )
                if chunk is not None:
                    bm25_candidates.append(chunk)
        strategies.append("bm25")

        exact_candidates = _exact_candidates(
            chunk_documents=chunk_documents,
            probe=probe,
            query=query,
            top_k=len(chunk_documents),
        )
        strategies.append("exact")
        structural_candidates = _structural_candidates(
            chunk_documents=chunk_documents,
            probe=probe,
        )
        strategies.append("structural")
        file_candidates = _file_scope_candidates(
            chunk_documents=chunk_documents,
            probe=probe,
        )
        if file_candidates:
            structural_candidates = [*file_candidates, *structural_candidates]
            strategies.append("file_scope")
        if probe.file_scope:
            semantic_candidates = _candidates_in_file(
                semantic_candidates,
                probe.file_scope,
            )
            bm25_candidates = _candidates_in_file(bm25_candidates, probe.file_scope)
            exact_candidates = _candidates_in_file(exact_candidates, probe.file_scope)
            structural_candidates = _candidates_in_file(
                structural_candidates,
                probe.file_scope,
            )
        semantic_candidates = _exclude_candidates(semantic_candidates, excluded_keys)
        bm25_candidates = _exclude_candidates(bm25_candidates, excluded_keys)
        exact_candidates = _exclude_candidates(exact_candidates, excluded_keys)
        structural_candidates = _exclude_candidates(
            structural_candidates,
            excluded_keys,
        )
        selected = _fuse_probe_candidates(
            semantic_candidates=semantic_candidates,
            bm25_candidates=bm25_candidates,
            exact_candidates=exact_candidates,
            structural_candidates=structural_candidates,
            probe=probe,
            query=query,
            top_k=top_k,
        )
        selected = _expand_related_candidates(
            selected=selected,
            chunk_documents=chunk_documents,
            probe=probe,
            top_k=top_k,
            excluded_keys=excluded_keys,
        )
        status = "ok" if selected else "no_candidate_evidence"
        return ProbeEvidenceBundle(
            probe=probe,
            retrieval_status=status,
            candidate_chunks=selected,
            strategies_used=strategies,
            strategy_candidate_counts={
                "semantic": len(_unique_candidates(semantic_candidates)),
                "bm25": len(_unique_candidates(bm25_candidates)),
                "exact": len(_unique_candidates(exact_candidates)),
                "structural": len(_unique_candidates(structural_candidates)),
            },
            selected_count_before_trim=len(selected),
        )

    def _code_retriever(self) -> CodeSemanticRetriever:
        if self.code_retriever is None:
            self.code_retriever = CodeSemanticRetriever()

        return self.code_retriever


class ProbeJudgeService:
    """Ask the LLM to judge backend-selected evidence batches."""

    def __init__(
        self,
        *,
        llm: Any,
        postgres_session: AsyncSession,
        database: AsyncIOMotorDatabase,
        max_probes_per_batch: int = 8,
        max_chunks_per_batch: int = 24,
        min_confidence: float = MIN_PROBE_ISSUE_CONFIDENCE,
    ) -> None:
        self.llm = llm
        self.postgres_session = postgres_session
        self.database = database
        self.max_probes_per_batch = max(1, max_probes_per_batch)
        self.max_chunks_per_batch = max(1, max_chunks_per_batch)
        self.min_confidence = min_confidence
        self.roadmap_by_id = {
            requirement.rule_id: requirement
            for requirement in load_roadmap_requirements()
        }

    async def judge_and_persist(
        self,
        *,
        job_id: UUID,
        bundles: list[ProbeEvidenceBundle],
        trace_writer: SyntheticTraceWriter,
    ) -> tuple[int, int, int]:
        """Judge evidence batches and persist accepted issue candidates."""

        created_count = 0
        rejected_count = 0
        judged_batches = 0
        for batch in _judge_batches(
            bundles,
            max_probes=self.max_probes_per_batch,
            max_chunks=self.max_chunks_per_batch,
        ):
            started_at = time.perf_counter()
            response = await self._judge_batch(batch)
            batch_created = 0
            batch_rejected = 0
            for candidate in response.candidates:
                created = await self._persist_candidate(
                    job_id=job_id,
                    candidate=candidate,
                    bundles=batch,
                )
                if created:
                    batch_created += 1
                else:
                    batch_rejected += 1

            judged_batches += 1
            created_count += batch_created
            rejected_count += batch_rejected
            await _write_probe_judge_trace(
                trace_writer=trace_writer,
                batch=batch,
                response=response,
                created_count=batch_created,
                rejected_count=batch_rejected,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )

        return judged_batches, created_count, rejected_count

    async def _judge_batch(
        self,
        batch: list[ProbeEvidenceBundle],
    ) -> ProbeJudgeResponse:
        prompt = _judge_prompt(batch)
        result = await self.llm.ainvoke(
            [
                (
                    "system",
                    "You are an evidence-only senior code review judge. "
                    "Judge only from the provided source chunks. Return JSON only.",
                ),
                ("user", prompt),
            ]
        )
        content = _message_content(result)
        payload = _json_object_from_text(content)
        return _probe_judge_response_from_payload(payload)

    async def _persist_candidate(
        self,
        *,
        job_id: UUID,
        candidate: ProbeJudgeIssueCandidate,
        bundles: list[ProbeEvidenceBundle],
    ) -> bool:
        if candidate.verdict != "issue":
            return False
        if candidate.confidence is None or candidate.confidence < self.min_confidence:
            return False
        if not _candidate_has_required_fields(candidate):
            return False

        file_path = candidate.file_path
        line_start = candidate.line_start
        line_end = candidate.line_end
        if file_path is None or line_start is None or line_end is None:
            return False

        evidence_chunk = _supporting_bundle_chunk(candidate, bundles)
        if evidence_chunk is None:
            return False

        category = _issue_category(candidate.category)
        severity = _issue_severity(candidate.severity)
        existing_issue = await self._find_existing_issue(
            job_id=job_id,
            file_path=file_path,
            line_start=line_start,
            category=category,
        )
        if existing_issue is not None:
            return False

        rule_id = _candidate_rule_id(
            candidate,
            bundles,
            roadmap_by_id=self.roadmap_by_id,
        )
        if _dependency_manifest_contradicts_candidate(
            candidate=candidate,
            rule=self.roadmap_by_id.get(rule_id or ""),
            evidence_chunk=evidence_chunk,
        ):
            return False
        issue_source = IssueSource.KB if rule_id else IssueSource.AI_REVIEW
        references = _candidate_references(
            category=category,
            rule_id=rule_id,
            roadmap_by_id=self.roadmap_by_id,
        )
        review_issue = ReviewIssue(
            job_id=job_id,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            severity=severity,
            category=category,
            title=str(candidate.title or "AI review finding")[:255],
            description=str(candidate.description or ""),
            suggestion=candidate.suggestion,
            source=issue_source,
            confidence=candidate.confidence,
            raw_output={
                "references": references,
                "probe_review": {
                    "probe_id": candidate.probe_id,
                    "rule_id": rule_id,
                    "claim_type": candidate.claim_type,
                    "supporting_evidence": [
                        item.model_dump(mode="json")
                        for item in candidate.supporting_evidence
                    ],
                    "contradicting_evidence": [
                        item.model_dump(mode="json")
                        for item in candidate.contradicting_evidence
                    ],
                    "source_context": _source_context(evidence_chunk),
                },
            },
        )
        self.postgres_session.add(review_issue)
        await self.postgres_session.commit()
        return True

    async def _find_existing_issue(
        self,
        *,
        job_id: UUID,
        file_path: str,
        line_start: int,
        category: IssueCategory,
    ) -> ReviewIssue | None:
        result = await self.postgres_session.execute(
            select(ReviewIssue)
            .where(
                ReviewIssue.job_id == job_id,
                ReviewIssue.file_path == file_path,
                ReviewIssue.line_start == line_start,
                ReviewIssue.category == category,
            )
            .order_by(ReviewIssue.created_at.asc())
        )
        return result.scalars().first()


async def run_backend_directed_probe_review(
    *,
    job_id: UUID,
    llm: Any,
    postgres_session: AsyncSession,
    mongodb_database: AsyncIOMotorDatabase,
    trace_writer: SyntheticTraceWriter,
    enable_semantic_search: bool,
    chunks_per_probe: int,
    max_chunks: int,
    defect_max_chunks: int,
    coverage_max_chunks: int,
    roadmap_max_chunks: int,
    max_probes_per_batch: int,
    max_chunks_per_batch: int,
    code_retriever: CodeSemanticRetriever | None = None,
) -> ProbeReviewResult:
    """Run the default backend-directed review path."""

    probes = await build_backend_probe_plan(
        job_id=job_id,
        postgres_session=postgres_session,
        database=mongodb_database,
    )
    job_options = await _load_job_options(postgres_session, job_id)
    retrieval_service = ProbeRetrievalService(
        database=mongodb_database,
        code_retriever=code_retriever,
        enable_semantic_search=enable_semantic_search,
        chunks_per_probe=chunks_per_probe,
        max_chunks=max_chunks,
        defect_max_chunks=defect_max_chunks,
        coverage_max_chunks=coverage_max_chunks,
        roadmap_max_chunks=roadmap_max_chunks,
        max_probes_per_batch=max_probes_per_batch,
        max_chunks_per_batch=max_chunks_per_batch,
        full_audit=get_review_mode(job_options) == REVIEW_MODE_FULL_AUDIT,
    )
    bundles = await retrieval_service.retrieve(
        job_id=job_id,
        probes=probes,
        trace_writer=trace_writer,
    )
    judge_service = ProbeJudgeService(
        llm=llm,
        postgres_session=postgres_session,
        database=mongodb_database,
        max_probes_per_batch=max_probes_per_batch,
        max_chunks_per_batch=max_chunks_per_batch,
    )
    (
        judged_batches,
        created_issues,
        rejected_candidates,
    ) = await judge_service.judge_and_persist(
        job_id=job_id,
        bundles=bundles,
        trace_writer=trace_writer,
    )
    retrieved_probes = sum(1 for bundle in bundles if bundle.candidate_chunks)
    no_evidence_probes = len(bundles) - retrieved_probes
    return ProbeReviewResult(
        total_probes=len(probes),
        retrieved_probes=retrieved_probes,
        no_evidence_probes=no_evidence_probes,
        judged_batches=judged_batches,
        created_issues=created_issues,
        rejected_candidates=rejected_candidates,
        handoff=(
            "Backend-directed probe review completed: "
            f"{len(probes)} probes, {retrieved_probes} with evidence, "
            f"{no_evidence_probes} without candidate evidence, "
            f"{judged_batches} judge batches, {created_issues} issues created, "
            f"{rejected_candidates} candidates rejected."
        ),
    )


async def build_backend_probe_plan(
    *,
    job_id: UUID,
    postgres_session: AsyncSession,
    database: AsyncIOMotorDatabase,
) -> list[ProbeDefinition]:
    """Build the same unified category probe plan without a tool loop."""

    structure_document = await database[FILE_ANALYSIS_RESULTS_COLLECTION].find_one(
        {"job_id": str(job_id)},
        sort=[("analyzed_at", -1)],
    )
    chunk_documents = await _load_chunk_documents(database, job_id)
    job_options = await _load_job_options(postgres_session, job_id)
    roadmap_context = _build_roadmap_context_without_vectorstore(job_options)
    file_tree = []
    if isinstance(structure_document, dict):
        file_tree = _as_list(structure_document.get("file_tree"))
    return build_semantic_audit_plan(
        roadmap_context=roadmap_context,
        files_to_review=_files_to_review(
            file_tree=file_tree,
            chunk_counts=_chunk_counts_by_file(chunk_documents),
            chunk_risks=_chunk_risks_by_file(chunk_documents),
        ),
        static_issues=[],
    )


async def _load_job_options(
    postgres_session: AsyncSession,
    job_id: UUID,
) -> dict[str, object] | None:
    result = await postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    return options if isinstance(options, dict) else None


def _build_roadmap_context_without_vectorstore(
    options: dict[str, object] | None,
) -> dict[str, object] | None:
    try:
        return build_roadmap_context(options, vectorstore=_RoadmapMetadataStore())
    except ValueError as error:
        logger.warning("Roadmap context unavailable for probe review: %s", error)
        return None


class _RoadmapMetadataStore:
    """Metadata-only store backed by the generated roadmap YAML."""

    def all_documents(self) -> list[Any]:
        return [
            _RoadmapMetadataDocument(requirement)
            for requirement in load_roadmap_requirements()
        ]


@dataclass(slots=True, frozen=True)
class _RoadmapMetadataDocument:
    requirement: RoadmapRequirement

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "doc_type": "roadmap_rule",
            "profile_id": "roadmap_bootcamp_v1",
            "rule_id": self.requirement.rule_id,
            "week": self.requirement.week,
            "priority": self.requirement.priority,
        }


async def _load_chunk_documents(
    database: AsyncIOMotorDatabase,
    job_id: UUID,
) -> list[dict[str, Any]]:
    documents = (
        await database[CHUNK_METADATA_COLLECTION]
        .find({"job_id": str(job_id)})
        .to_list(length=None)
    )
    return [document for document in documents if isinstance(document, dict)]


def _repo_branch_key_from_documents(documents: list[dict[str, Any]]) -> str | None:
    for document in documents:
        repo_branch_key = document.get("repo_branch_key")
        if isinstance(repo_branch_key, str) and repo_branch_key:
            return repo_branch_key

    return None


def _index_generation_key_from_documents(
    documents: list[dict[str, Any]],
) -> str | None:
    for document in documents:
        index_generation_key = document.get("index_generation_key")
        if isinstance(index_generation_key, str) and index_generation_key:
            return index_generation_key

    return None


def _build_bm25_index(chunk_documents: list[dict[str, Any]]) -> BM25Index:
    documents: list[BM25Document] = []
    for document in chunk_documents:
        content = document.get("chunk_text")
        if not isinstance(content, str) or not content:
            continue
        searchable = " ".join(
            [
                str(document.get("file_path") or ""),
                str(document.get("module") or ""),
                str(document.get("function_name") or ""),
                str(document.get("class_name") or ""),
                " ".join(str(value) for value in document.get("imports", [])),
                content,
            ]
        )
        documents.append(
            BM25Document(
                id=_document_key(document),
                content=searchable,
                metadata=document,
            )
        )

    index = BM25Index()
    index.add_documents(documents)
    return index


async def _semantic_search(
    *,
    retriever: CodeSemanticRetriever,
    job_id: UUID,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    query: str,
    top_k: int,
) -> list[Any]:
    return await _to_thread_search(
        retriever,
        query=query,
        job_id=job_id,
        repo_branch_key=repo_branch_key,
        index_generation_key=index_generation_key,
        top_k=top_k,
    )


async def _semantic_search_many(
    *,
    retriever: CodeSemanticRetriever,
    requests: list[CodeSemanticSearchRequest],
) -> list[list[Any]]:
    return await _to_thread_search_many(retriever, requests=requests)


async def _to_thread_search_many(
    retriever: CodeSemanticRetriever,
    *,
    requests: list[CodeSemanticSearchRequest],
) -> list[list[Any]]:
    import asyncio

    return await asyncio.to_thread(retriever.search_many, requests)


async def _to_thread_search(
    retriever: CodeSemanticRetriever,
    *,
    query: str,
    job_id: UUID,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    top_k: int,
) -> list[Any]:
    import asyncio

    return await asyncio.to_thread(
        retriever.search,
        query=query,
        job_id=job_id,
        repo_branch_key=repo_branch_key,
        index_generation_key=index_generation_key,
        top_k=top_k,
    )


def _candidate_from_semantic_result(
    result: Any,
    *,
    probe: ProbeDefinition,
    query: str,
) -> ProbeCandidateChunk | None:
    return _candidate_from_document(
        dict(result.metadata),
        probe=probe,
        query=query,
        semantic_score=float(result.semantic_score),
        lexical_score=0.0,
        content=str(result.content),
        strategy="semantic",
    )


def _candidate_from_document(
    document: dict[str, Any],
    *,
    probe: ProbeDefinition,
    query: str,
    semantic_score: float,
    lexical_score: float,
    content: str | None = None,
    strategy: str = "exact",
) -> ProbeCandidateChunk | None:
    chunk_text = content if content is not None else document.get("chunk_text")
    if not isinstance(chunk_text, str) or not chunk_text:
        return None

    file_path = document.get("file_path")
    chunk_index = document.get("chunk_index")
    line_start = document.get("line_start")
    line_end = document.get("line_end")
    if (
        not isinstance(file_path, str)
        or not isinstance(chunk_index, int)
        or not isinstance(line_start, int)
        or not isinstance(line_end, int)
    ):
        return None

    path_score = _path_score(file_path=file_path, probe=probe, query=query)
    static_score = 0.0
    risk_score = _risk_score(document, probe)
    exact_score = _exact_score(query, chunk_text, file_path)
    final_score = (
        semantic_score * 0.45
        + lexical_score * 0.25
        + exact_score * 0.2
        + path_score
        + risk_score
    )
    return ProbeCandidateChunk(
        file_path=file_path,
        chunk_index=chunk_index,
        line_start=line_start,
        line_end=line_end,
        language=str(document.get("language") or "text"),
        risk_area=str(document.get("risk_area") or "general"),
        content=chunk_text,
        semantic_score=semantic_score,
        lexical_score=max(lexical_score, exact_score),
        path_score=path_score,
        static_score=static_score,
        final_score=final_score,
        strategies=(strategy,),
    )


def _exact_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
    query: str,
    top_k: int,
) -> list[ProbeCandidateChunk]:
    candidates: list[ProbeCandidateChunk] = []
    for document in chunk_documents:
        chunk_text = document.get("chunk_text")
        if not isinstance(chunk_text, str):
            continue
        lexical_score = _exact_score(
            query,
            chunk_text,
            str(document.get("file_path") or ""),
        )
        if lexical_score <= 0:
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=query,
            semantic_score=0.0,
            lexical_score=lexical_score,
            strategy="exact",
        )
        if candidate is not None:
            candidates.append(candidate)

    return sorted(candidates, key=lambda item: item.final_score, reverse=True)[:top_k]


def _structural_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
) -> list[ProbeCandidateChunk]:
    matcher = _STRUCTURAL_MATCHERS.get(probe.probe_id)
    if matcher is None:
        return []

    candidates: list[ProbeCandidateChunk] = []
    for document in chunk_documents:
        if str(document.get("language") or "").lower() != "python":
            continue
        content = document.get("chunk_text")
        if not isinstance(content, str) or not matcher(content.lower()):
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=probe.primary_query,
            semantic_score=0.0,
            lexical_score=1.0,
            strategy="structural",
        )
        if candidate is not None:
            chunk_type = str(document.get("chunk_type") or "").lower()
            function_bonus = 0.4 if chunk_type == "function" else 0.0
            candidates.append(
                replace(
                    candidate,
                    final_score=candidate.final_score + 0.5 + function_bonus,
                )
            )
    return sorted(candidates, key=_structural_candidate_rank, reverse=True)


def _structural_candidate_rank(
    candidate: ProbeCandidateChunk,
) -> tuple[float, int, str, int]:
    line_span = candidate.line_end - candidate.line_start
    return (
        candidate.final_score,
        -line_span,
        candidate.file_path,
        -candidate.chunk_index,
    )


def _file_scope_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
) -> list[ProbeCandidateChunk]:
    if probe.file_scope is None:
        return []

    candidates: list[ProbeCandidateChunk] = []
    for document in chunk_documents:
        if document.get("file_path") != probe.file_scope:
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=probe.primary_query,
            semantic_score=0.0,
            lexical_score=_exact_score(
                probe.primary_query,
                str(document.get("chunk_text") or ""),
                probe.file_scope,
            ),
            strategy="file_scope",
        )
        if candidate is not None:
            candidates.append(
                replace(candidate, final_score=candidate.final_score + 0.4)
            )
    return sorted(candidates, key=_file_scope_candidate_rank, reverse=True)


def _file_scope_candidate_rank(
    candidate: ProbeCandidateChunk,
) -> tuple[int, float, int]:
    is_function = int(candidate.line_end > candidate.line_start)
    return is_function, candidate.final_score, -candidate.chunk_index


def _candidates_in_file(
    candidates: list[ProbeCandidateChunk],
    file_path: str,
) -> list[ProbeCandidateChunk]:
    return [candidate for candidate in candidates if candidate.file_path == file_path]


def _exclude_candidates(
    candidates: list[ProbeCandidateChunk],
    excluded_keys: set[tuple[str, int]],
) -> list[ProbeCandidateChunk]:
    if not excluded_keys:
        return candidates
    return [candidate for candidate in candidates if candidate.key not in excluded_keys]


def _contains_all(content: str, *terms: str) -> bool:
    return all(term in content for term in terms)


def _contains_any(content: str, *terms: str) -> bool:
    return any(term in content for term in terms)


def _matches_sql_injection(content: str) -> bool:
    has_sink = _contains_any(content, "execute(", "from_statement(", "raw(")
    has_construction = _contains_any(content, 'f"', "f'", ".format(", " + ")
    return has_sink and has_construction


def _matches_command_injection(content: str) -> bool:
    return _contains_any(content, "subprocess", "os.system", "popen(") and (
        "shell=true" in content or _contains_any(content, 'f"', "f'", ".format(")
    )


def _matches_ssrf(content: str) -> bool:
    has_client = _contains_any(
        content,
        "httpx",
        "requests.",
        "client.get(",
        "axios",
        "fetch(",
    )
    return has_client and _contains_any(content, "url", "uri", "webhook")


def _matches_object_authorization(content: str) -> bool:
    return _contains_all(content, "current_user", "get_by_id") and _contains_any(
        content,
        "_id",
        "id:",
    )


def _matches_role_authorization(content: str) -> bool:
    return "admin" in content and "get_current_user" in content


def _matches_mass_assignment(content: str) -> bool:
    return "setattr(" in content and _contains_any(
        content, "payload", ".items()", "dict"
    )


def _matches_weak_hash(content: str) -> bool:
    return "password" in content and _contains_any(content, "md5(", "sha1(")


def _matches_reset_token(content: str) -> bool:
    return (
        "reset" in content
        and "token" in content
        and _contains_any(
            content,
            "redis.set(",
            ".set(",
            "setex(",
        )
    )


def _matches_refresh_validation(content: str) -> bool:
    return "refresh" in content and _contains_any(content, "jwt", "decode(", "token")


def _matches_logout(content: str) -> bool:
    return "logout" in content and _contains_any(
        content, "revoke", "blacklist", "success"
    )


def _matches_race(content: str) -> bool:
    has_resource = _contains_any(content, "stock", "inventory", "quantity")
    has_read = _contains_any(content, "get_by_", "select(")
    has_write = "update_" in content
    has_subtraction_assignment = (
        re.search(
            r"=\s*[a-z_][a-z0-9_.]*\s+-\s+[a-z_]",
            content,
        )
        is not None
    )
    return has_resource and has_read and has_write and has_subtraction_assignment


def _matches_transaction(content: str) -> bool:
    return "except" in content and _contains_any(
        content, "commit(", "update_", "create_"
    )


def _matches_n_plus_one(content: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Call) and _is_io_call(child):
                    return True
    return False


def _is_io_call(call: ast.Call) -> bool:
    try:
        call_name = ast.unparse(call.func).lower()
    except ValueError:
        return False
    sink_names = (
        ".execute",
        ".query",
        ".fetch",
        ".find",
        ".get_by_",
        "repository.",
        "repo.",
        "client.",
    )
    return any(sink in call_name for sink in sink_names)


def _matches_pagination(content: str) -> bool:
    loads_all = _contains_any(content, ".all()", "scalars().all", "list(")
    in_memory = _contains_any(content, "filtered_", "[p for ", "[item for ", "offset :")
    return loads_all and in_memory


def _matches_resource_lifecycle(content: str) -> bool:
    return _contains_any(
        content, "create_engine(", "create_async_engine("
    ) and _contains_any(
        content,
        "def ",
        "async def ",
    )


_STRUCTURAL_MATCHERS: dict[str, Any] = {
    "security.sql_nosql_injection": _matches_sql_injection,
    "security.command_injection": _matches_command_injection,
    "security.ssrf_external_calls": _matches_ssrf,
    "security.object_authorization": _matches_object_authorization,
    "security.role_authorization": _matches_role_authorization,
    "security.mass_assignment": _matches_mass_assignment,
    "security.weak_password_hash": _matches_weak_hash,
    "security.reset_token_lifecycle": _matches_reset_token,
    "security.refresh_token_validation": _matches_refresh_validation,
    "security.logout_revocation": _matches_logout,
    "bug.async_concurrency": _matches_race,
    "bug.state_transaction_consistency": _matches_transaction,
    "performance.n_plus_one": _matches_n_plus_one,
    "performance.pagination_bounds": _matches_pagination,
    "maintainability.resource_lifecycle": _matches_resource_lifecycle,
}


def _expand_related_candidates(
    *,
    selected: list[ProbeCandidateChunk],
    chunk_documents: list[dict[str, Any]],
    probe: ProbeDefinition,
    top_k: int,
    excluded_keys: set[tuple[str, int]],
) -> list[ProbeCandidateChunk]:
    """Add at most two directly called function chunks as supporting context."""

    call_names = {
        name.lower()
        for candidate in selected
        for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", candidate.content)
        if name.lower() not in _IGNORED_CALL_NAMES
    }
    if not call_names:
        return selected[:top_k]
    if len(selected) >= top_k:
        return selected[:top_k]

    related: list[ProbeCandidateChunk] = []
    selected_keys = {candidate.key for candidate in selected}
    for document in chunk_documents:
        function_name = str(document.get("function_name") or "").lower()
        if not function_name or function_name not in call_names:
            continue
        key = (str(document.get("file_path") or ""), document.get("chunk_index"))
        if key in selected_keys or key in excluded_keys:
            continue
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query=function_name,
            semantic_score=0.0,
            lexical_score=1.0,
            strategy="related",
        )
        if candidate is None:
            continue
        related.append(candidate)
        if len(related) >= MAX_RELATED_CHUNKS:
            break

    if not related:
        return selected[:top_k]
    remaining_slots = top_k - len(selected)
    return [*selected, *related[:remaining_slots]]


_IGNORED_CALL_NAMES = {
    "dict",
    "float",
    "int",
    "len",
    "list",
    "max",
    "min",
    "print",
    "str",
    "sum",
    "super",
}


def _fuse_probe_candidates(
    *,
    semantic_candidates: list[ProbeCandidateChunk],
    bm25_candidates: list[ProbeCandidateChunk],
    exact_candidates: list[ProbeCandidateChunk],
    structural_candidates: list[ProbeCandidateChunk],
    probe: ProbeDefinition,
    query: str,
    top_k: int,
) -> list[ProbeCandidateChunk]:
    """Fuse retrieval ranks while reserving evidence from distinct strategies."""

    candidate_by_key: dict[tuple[str, int], ProbeCandidateChunk] = {}
    rrf_scores: dict[tuple[str, int], float] = {}
    weights = _adaptive_rrf_weights(probe=probe, query=query)

    for strategy, candidates in (
        ("semantic", semantic_candidates),
        ("bm25", bm25_candidates),
        ("exact", exact_candidates),
        ("structural", structural_candidates),
    ):
        for rank, candidate in enumerate(_unique_candidates(candidates), start=1):
            candidate_by_key[candidate.key] = _merge_candidate(
                candidate_by_key.get(candidate.key),
                candidate,
            )
            rrf_scores[candidate.key] = rrf_scores.get(candidate.key, 0.0) + (
                weights[strategy] / (RRF_K + rank)
            )

    ranked = [
        replace(
            candidate,
            final_score=rrf_scores.get(key, 0.0) + _bounded_code_prior(candidate),
        )
        for key, candidate in candidate_by_key.items()
    ]
    ranked.sort(key=lambda candidate: candidate.final_score, reverse=True)
    reserved = _strategy_quota_candidates(
        structural_candidates=structural_candidates,
        lexical_candidates=[*bm25_candidates, *exact_candidates],
        semantic_candidates=semantic_candidates,
        top_k=top_k,
    )
    return _fill_diverse_candidates(reserved=reserved, ranked=ranked, top_k=top_k)


def _unique_candidates(
    candidates: list[ProbeCandidateChunk],
) -> list[ProbeCandidateChunk]:
    seen: set[tuple[str, int]] = set()
    unique: list[ProbeCandidateChunk] = []
    for candidate in sorted(
        candidates,
        key=lambda item: item.final_score,
        reverse=True,
    ):
        if candidate.key in seen:
            continue
        nested_index = next(
            (
                index
                for index, current in enumerate(unique)
                if _chunks_are_nested(candidate, current)
            ),
            None,
        )
        if nested_index is not None and _line_span(candidate) >= _line_span(
            unique[nested_index]
        ):
            continue
        seen.add(candidate.key)
        if nested_index is None:
            unique.append(candidate)
        else:
            unique[nested_index] = candidate
    return sorted(unique, key=lambda item: item.final_score, reverse=True)


def _line_span(candidate: ProbeCandidateChunk) -> int:
    return candidate.line_end - candidate.line_start


def _chunks_are_nested(
    first: ProbeCandidateChunk,
    second: ProbeCandidateChunk,
) -> bool:
    if first.file_path != second.file_path:
        return False
    return _range_contains(
        outer_start=first.line_start,
        outer_end=first.line_end,
        inner_start=second.line_start,
        inner_end=second.line_end,
    ) or _range_contains(
        outer_start=second.line_start,
        outer_end=second.line_end,
        inner_start=first.line_start,
        inner_end=first.line_end,
    )


def _merge_candidate(
    current: ProbeCandidateChunk | None,
    incoming: ProbeCandidateChunk,
) -> ProbeCandidateChunk:
    if current is None:
        return incoming

    return replace(
        current,
        semantic_score=max(current.semantic_score, incoming.semantic_score),
        lexical_score=max(current.lexical_score, incoming.lexical_score),
        path_score=max(current.path_score, incoming.path_score),
        strategies=tuple(sorted(set(current.strategies) | set(incoming.strategies))),
    )


def _adaptive_rrf_weights(
    *,
    probe: ProbeDefinition,
    query: str,
) -> dict[str, float]:
    category = probe.category
    if _looks_like_symbol_query(query):
        return {"semantic": 0.25, "bm25": 0.35, "exact": 0.2, "structural": 0.5}
    if category in {"security", "requirement"}:
        return {"semantic": 0.3, "bm25": 0.3, "exact": 0.2, "structural": 0.55}
    return {"semantic": 0.35, "bm25": 0.3, "exact": 0.2, "structural": 0.45}


def _looks_like_symbol_query(query: str) -> bool:
    terms = re.findall(r"[A-Za-z_][A-Za-z0-9_:.]*", query)
    if not terms:
        return False
    symbolish_terms = sum(
        1
        for term in terms
        if (
            "::" in term
            or "." in term
            or "_" in term
            or (
                any(char.islower() for char in term)
                and any(char.isupper() for char in term)
            )
        )
    )
    return symbolish_terms >= max(1, len(terms) // 2)


def _bounded_code_prior(candidate: ProbeCandidateChunk) -> float:
    return max(
        -0.12,
        min(
            0.18,
            candidate.path_score * 0.25
            + min(candidate.semantic_score, 1.0) * 0.02
            + min(candidate.lexical_score, 1.0) * 0.02,
        ),
    )


def _strategy_quota_candidates(
    *,
    structural_candidates: list[ProbeCandidateChunk],
    lexical_candidates: list[ProbeCandidateChunk],
    semantic_candidates: list[ProbeCandidateChunk],
    top_k: int,
) -> list[ProbeCandidateChunk]:
    selected: list[ProbeCandidateChunk] = []
    for candidates in (
        structural_candidates[:2],
        lexical_candidates[:2],
        semantic_candidates[:2],
    ):
        for candidate in _unique_candidates(candidates):
            if len(selected) >= top_k:
                return selected
            if _can_add_candidate(selected, candidate):
                selected.append(candidate)
    return selected


def _fill_diverse_candidates(
    *,
    reserved: list[ProbeCandidateChunk],
    ranked: list[ProbeCandidateChunk],
    top_k: int,
) -> list[ProbeCandidateChunk]:
    if top_k <= 0:
        return []

    max_per_file = max(1, top_k // 2)
    selected = list(reserved)
    selected_keys = {candidate.key for candidate in selected}
    per_file_count: dict[str, int] = {}
    for candidate in selected:
        per_file_count[candidate.file_path] = (
            per_file_count.get(candidate.file_path, 0) + 1
        )
    deferred: list[ProbeCandidateChunk] = []

    for candidate in ranked:
        if len(selected) >= top_k:
            return selected
        if candidate.key in selected_keys:
            continue
        if not _can_add_candidate(selected, candidate):
            continue
        if per_file_count.get(candidate.file_path, 0) >= max_per_file:
            deferred.append(candidate)
            continue
        selected.append(candidate)
        selected_keys.add(candidate.key)
        per_file_count[candidate.file_path] = (
            per_file_count.get(candidate.file_path, 0) + 1
        )

    for candidate in deferred:
        if len(selected) >= top_k:
            return selected
        if candidate.key in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(candidate.key)

    return selected


def _can_add_candidate(
    selected: list[ProbeCandidateChunk],
    candidate: ProbeCandidateChunk,
) -> bool:
    if any(current.key == candidate.key for current in selected):
        return False
    return not any(_chunks_are_nested(candidate, current) for current in selected)


def _range_contains(
    *,
    outer_start: int,
    outer_end: int,
    inner_start: int,
    inner_end: int,
) -> bool:
    return outer_start <= inner_start and outer_end >= inner_end


def _exact_score(query: str, content: str, file_path: str) -> float:
    query_terms = _important_terms(query)
    if not query_terms:
        return 0.0
    searchable = f"{file_path}\n{content}".lower()
    matched = sum(1 for term in query_terms if term in searchable)
    return min(1.0, matched / max(len(query_terms), 1))


def _important_terms(query: str) -> list[str]:
    return [
        term
        for term in tokenize(query)
        if len(term) >= MIN_IMPORTANT_QUERY_TERM_LENGTH and term not in STOP_WORDS
    ][:32]


def _path_score(
    *,
    file_path: str,
    probe: ProbeDefinition,
    query: str,
) -> float:
    normalized_path = file_path.replace("\\", "/").lower()
    category = probe.category
    hints = PATH_HINTS_BY_CATEGORY.get(category, ())
    score = 0.0
    if any(hint in normalized_path for hint in hints):
        score += 0.2
    if any(term in normalized_path for term in _important_terms(query)[:10]):
        score += 0.1
    if _is_non_runtime_path(normalized_path):
        score -= 0.45
    return score


def _risk_score(document: dict[str, Any], probe: ProbeDefinition) -> float:
    chunk_risk = str(document.get("risk_area") or "")
    probe_risk = probe.risk_area
    category = probe.category
    if probe_risk and probe_risk == chunk_risk:
        return 0.12
    if category == "security" and chunk_risk in {"security", "config", "api"}:
        return 0.12
    if category == "performance" and chunk_risk in {"database", "api"}:
        return 0.1
    return 0.0


def _is_non_runtime_path(normalized_path: str) -> bool:
    filename = normalized_path.rsplit("/", 1)[-1]
    return (
        "/tests/" in normalized_path
        or "/test/" in normalized_path
        or "/docs/" in normalized_path
        or filename.startswith("test_")
        or filename.endswith((".md", ".rst"))
    )


def _trim_bundles(
    bundles: list[ProbeEvidenceBundle],
    *,
    max_chunks: int,
) -> list[ProbeEvidenceBundle]:
    total_chunks = sum(len(bundle.candidate_chunks) for bundle in bundles)
    if total_chunks <= max_chunks:
        return bundles

    kept: dict[str, set[tuple[str, int]]] = {
        _probe_id(bundle.probe): set() for bundle in bundles
    }
    remaining_slots = _reserve_probe_candidates(
        bundles=bundles,
        kept=kept,
        max_chunks=max_chunks,
    )

    scored_chunks: list[tuple[tuple[int, float], str, ProbeCandidateChunk]] = []
    for bundle in bundles:
        probe_id = _probe_id(bundle.probe)
        for chunk in bundle.candidate_chunks:
            if chunk.key in kept[probe_id]:
                continue
            scored_chunks.append((_trim_rank(bundle.probe, chunk), probe_id, chunk))

    for _rank, probe_id, chunk in sorted(
        scored_chunks,
        key=_trim_scored_chunk_sort_key,
        reverse=True,
    ):
        if remaining_slots <= 0:
            break
        kept[probe_id].add(chunk.key)
        remaining_slots -= 1

    trimmed: list[ProbeEvidenceBundle] = []
    for bundle in bundles:
        probe_id = _probe_id(bundle.probe)
        chunks = [
            chunk for chunk in bundle.candidate_chunks if chunk.key in kept[probe_id]
        ]
        status = bundle.retrieval_status
        if bundle.candidate_chunks and not chunks:
            status = "trimmed_by_global_cap"
        trimmed.append(
            replace(
                bundle,
                retrieval_status=status,
                candidate_chunks=chunks,
                trimmed_count=bundle.trimmed_count
                + len(bundle.candidate_chunks)
                - len(chunks),
            )
        )

    return trimmed


def _reserve_probe_candidates(
    *,
    bundles: list[ProbeEvidenceBundle],
    kept: dict[str, set[tuple[str, int]]],
    max_chunks: int,
) -> int:
    remaining_slots = max_chunks
    for bundle in bundles:
        if remaining_slots <= 0 or not bundle.candidate_chunks:
            break
        kept[_probe_id(bundle.probe)].add(bundle.candidate_chunks[0].key)
        remaining_slots -= 1

    for bundle in bundles:
        if remaining_slots <= 0:
            break
        probe_id = _probe_id(bundle.probe)
        kept_structural_count = sum(
            "structural" in chunk.strategies and chunk.key in kept[probe_id]
            for chunk in bundle.candidate_chunks
        )
        structural_slots = max(0, 2 - kept_structural_count)
        structural_candidates = [
            chunk
            for chunk in bundle.candidate_chunks
            if "structural" in chunk.strategies and chunk.key not in kept[probe_id]
        ]
        for chunk in structural_candidates[:structural_slots]:
            if remaining_slots <= 0:
                return 0
            kept[probe_id].add(chunk.key)
            remaining_slots -= 1
    return remaining_slots


def _trim_bundles_by_lane(
    bundles: list[ProbeEvidenceBundle],
    *,
    lane_max_chunks: dict[ProbeLane, int],
    max_chunks: int,
) -> list[ProbeEvidenceBundle]:
    """Apply independent lane caps before the global safety cap."""

    trimmed_by_id: dict[str, ProbeEvidenceBundle] = {}
    for lane in ProbeLane:
        lane_bundles = [bundle for bundle in bundles if bundle.probe.lane is lane]
        for bundle in _trim_bundles(
            lane_bundles,
            max_chunks=lane_max_chunks[lane],
        ):
            trimmed_by_id[bundle.probe.probe_id] = bundle

    ordered = [trimmed_by_id[bundle.probe.probe_id] for bundle in bundles]
    return _trim_bundles(ordered, max_chunks=max_chunks)


def _full_audit_bundles(
    *,
    chunk_documents: list[dict[str, Any]],
    existing_bundles: list[ProbeEvidenceBundle],
) -> list[ProbeEvidenceBundle]:
    """Return direct evidence bundles for every chunk not already scheduled."""

    existing_keys = {
        chunk.key for bundle in existing_bundles for chunk in bundle.candidate_chunks
    }
    candidates_by_file: dict[str, list[ProbeCandidateChunk]] = {}
    for document in chunk_documents:
        key = (document.get("file_path"), document.get("chunk_index"))
        if key in existing_keys:
            continue
        file_path = document.get("file_path")
        if not isinstance(file_path, str):
            continue
        risk_area = str(document.get("risk_area") or "general")
        probe = _full_audit_probe(file_path=file_path, risk_area=risk_area, index=0)
        candidate = _candidate_from_document(
            document,
            probe=probe,
            query="full source audit",
            semantic_score=0.0,
            lexical_score=1.0,
            strategy="full_audit",
        )
        if candidate is not None:
            candidates_by_file.setdefault(file_path, []).append(candidate)

    bundles: list[ProbeEvidenceBundle] = []
    for file_path, candidates in sorted(candidates_by_file.items()):
        risk_area = candidates[0].risk_area
        for index in range(0, len(candidates), FULL_AUDIT_CHUNKS_PER_PROBE):
            chunk_slice = candidates[index : index + FULL_AUDIT_CHUNKS_PER_PROBE]
            probe = _full_audit_probe(
                file_path=file_path,
                risk_area=risk_area,
                index=index // FULL_AUDIT_CHUNKS_PER_PROBE,
            )
            bundles.append(
                ProbeEvidenceBundle(
                    probe=probe,
                    retrieval_status="ok",
                    candidate_chunks=chunk_slice,
                    strategies_used=["full_audit"],
                    strategy_candidate_counts={"full_audit": len(chunk_slice)},
                    selected_count_before_trim=len(chunk_slice),
                )
            )
    return bundles


def _full_audit_probe(
    *,
    file_path: str,
    risk_area: str,
    index: int,
) -> ProbeDefinition:
    category = {
        "security": "security",
        "api": "bug",
        "database": "performance",
    }.get(risk_area, "maintainability")
    return ProbeDefinition(
        probe_id=(
            "coverage.full_audit."
            f"{_sha256(file_path.encode())[:TRACE_ID_HASH_LENGTH]}.{index}"
        ),
        lane=ProbeLane.COVERAGE,
        category=category,
        priority="medium",
        risk_area=risk_area,
        retrieval_queries=("full source audit",),
        lexical_terms=(),
        judge_question=(
            "Does this source contain a concrete security, correctness, performance, "
            "or resource-lifecycle defect?"
        ),
        top_k=FULL_AUDIT_CHUNKS_PER_PROBE,
        file_scope=file_path,
        source_kinds=("full_audit",),
        reason="full_audit",
        probe_kind="coverage",
    )


def _trim_rank(
    probe: ProbeDefinition,
    chunk: ProbeCandidateChunk,
) -> tuple[int, float]:
    category = probe.category
    priority = probe.priority
    category_rank = {"security": 4, "bug": 3, "performance": 2}.get(category, 1)
    priority_rank = {"high": 3, "medium": 2, "low": 1}.get(priority, 1)
    if category == "style":
        category_rank = 0
    if category == "maintainability" and priority == "low":
        category_rank = 0
    return category_rank + priority_rank, chunk.final_score


def _trim_scored_chunk_sort_key(
    scored_chunk: tuple[tuple[int, float], str, ProbeCandidateChunk],
) -> tuple[int, float, str, str, int]:
    rank, probe_id, chunk = scored_chunk
    return rank[0], rank[1], probe_id, chunk.file_path, chunk.chunk_index


def _judge_batches(
    bundles: list[ProbeEvidenceBundle],
    *,
    max_probes: int,
    max_chunks: int,
) -> list[list[ProbeEvidenceBundle]]:
    batches: list[list[ProbeEvidenceBundle]] = []
    current_batch: list[ProbeEvidenceBundle] = []
    current_chunk_count = 0
    current_lane: ProbeLane | None = None
    for bundle in bundles:
        if not bundle.candidate_chunks:
            continue
        bundle_chunk_count = len(bundle.candidate_chunks)
        if current_batch and (
            bundle.probe.lane is not current_lane
            or len(current_batch) >= max_probes
            or current_chunk_count + bundle_chunk_count > max_chunks
        ):
            batches.append(current_batch)
            current_batch = []
            current_chunk_count = 0
        current_batch.append(bundle)
        current_chunk_count += bundle_chunk_count
        current_lane = bundle.probe.lane

    if current_batch:
        batches.append(current_batch)

    return batches


def _judge_prompt(batch: list[ProbeEvidenceBundle]) -> str:
    payload = {
        "instruction": (
            "For each probe, decide whether the evidence proves a real issue. "
            "Do not invent files, rules, or missing behavior outside the chunks. "
            "Return at least one candidate for every probe; use no_issue or "
            "uncertain when evidence does not prove an issue. Preserve probe_id. "
            "Return exactly one JSON object and no markdown. Persistable issues "
            "require confidence >= 0.7."
        ),
        "format_rules": [
            "candidates must be an array.",
            "supporting_evidence and contradicting_evidence must be arrays.",
            "Use [] for empty evidence arrays; never use null or a string.",
            "Every evidence item must include file_path, chunk_index, line_start, "
            "and line_end.",
            "Use verdict values only: issue, no_issue, uncertain.",
        ],
        "output_schema": _judge_output_schema(),
        "probes": [_bundle_for_prompt(bundle) for bundle in batch],
    }
    return json.dumps(payload, ensure_ascii=False)


def _judge_output_schema() -> dict[str, object]:
    evidence_reference_schema = {
        "file_path": "string",
        "chunk_index": "integer",
        "line_start": "integer >= 1",
        "line_end": "integer >= 1",
        "rationale": "string | null",
    }
    return {
        "candidates": [
            {
                "verdict": "issue | no_issue | uncertain",
                "claim_type": "string | null",
                "title": "string | null",
                "description": "string | null",
                "suggestion": "string | null",
                "severity": "critical | high | medium | low | info | null",
                "category": (
                    "security | bug | performance | maintainability | style | "
                    "requirement | null"
                ),
                "confidence": "number between 0 and 1 | null",
                "file_path": "string | null",
                "line_start": "integer >= 1 | null",
                "line_end": "integer >= 1 | null",
                "supporting_evidence": [evidence_reference_schema],
                "contradicting_evidence": [evidence_reference_schema],
                "rule_id": "string | null",
                "probe_id": "string | null",
            }
        ]
    }


def _bundle_for_prompt(bundle: ProbeEvidenceBundle) -> dict[str, object]:
    return {
        "probe": bundle.probe.prompt_payload(),
        "retrieval_status": bundle.retrieval_status,
        "candidate_chunks": [
            {
                "file_path": chunk.file_path,
                "chunk_index": chunk.chunk_index,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "language": chunk.language,
                "risk_area": chunk.risk_area,
                "scores": {
                    "semantic": round(chunk.semantic_score, 4),
                    "lexical": round(chunk.lexical_score, 4),
                    "path": round(chunk.path_score, 4),
                    "static": round(chunk.static_score, 4),
                    "final": round(chunk.final_score, 4),
                },
                "content": _numbered_content(chunk),
            }
            for chunk in bundle.candidate_chunks
        ],
    }


def _numbered_content(chunk: ProbeCandidateChunk) -> str:
    lines = chunk.content.splitlines()
    return "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(lines, start=chunk.line_start)
    )


async def _write_probe_retrieval_trace(
    *,
    trace_writer: SyntheticTraceWriter,
    probe: ProbeDefinition,
    bundle: ProbeEvidenceBundle,
    duration_ms: int,
) -> None:
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_RETRIEVAL_TOOL_NAME,
        tool_input={
            "probe_id": probe.probe_id,
            "lane": probe.lane.value,
            "category": probe.category,
            "related_rule_ids": list(probe.related_rule_ids),
            "retrieval_queries": list(probe.retrieval_queries),
            "query": probe.primary_query,
        },
        output={
            "status": bundle.retrieval_status,
            "summary": "source content redacted from tool trace",
            "duration_ms": duration_ms,
            "strategies_used": bundle.strategies_used,
            "candidate_counts": bundle.strategy_candidate_counts,
            "selected_count": bundle.selected_count_before_trim,
            "trimmed_count": bundle.trimmed_count,
            "sent_to_judge": len(bundle.candidate_chunks),
            "result_count": len(bundle.candidate_chunks),
            "results": [_chunk_trace(chunk) for chunk in bundle.candidate_chunks],
        },
    )


async def _write_probe_judge_trace(
    *,
    trace_writer: SyntheticTraceWriter,
    batch: list[ProbeEvidenceBundle],
    response: ProbeJudgeResponse,
    created_count: int,
    rejected_count: int,
    duration_ms: int,
) -> None:
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_JUDGE_TOOL_NAME,
        tool_input={
            "probe_ids": [_probe_id(bundle.probe) for bundle in batch],
            "probe_count": len(batch),
            "chunk_count": sum(len(bundle.candidate_chunks) for bundle in batch),
        },
        output={
            "status": "ok",
            "duration_ms": duration_ms,
            "candidate_count": len(response.candidates),
            "schema_rejected_count": response.schema_rejected_count,
            "created_count": created_count,
            "rejected_count": rejected_count,
            "candidates": [
                candidate.model_dump(
                    mode="json",
                    exclude={"description", "suggestion"},
                )
                for candidate in response.candidates
            ],
        },
    )


def _chunk_trace(chunk: ProbeCandidateChunk) -> dict[str, object]:
    content_bytes = chunk.content.encode("utf-8")
    return {
        "status": "ok",
        "summary": "source content redacted from tool trace",
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "semantic_score": chunk.semantic_score,
        "lexical_score": chunk.lexical_score,
        "final_score": chunk.final_score,
        "content_sha256": _sha256(content_bytes),
        "content_size": len(content_bytes),
        "chunk_key": [chunk.file_path, chunk.chunk_index],
    }


def _supporting_bundle_chunk(
    candidate: ProbeJudgeIssueCandidate,
    bundles: list[ProbeEvidenceBundle],
) -> ProbeCandidateChunk | None:
    file_path = candidate.file_path
    line_start = candidate.line_start
    line_end = candidate.line_end
    if file_path is None or line_start is None or line_end is None:
        return None

    if not candidate.supporting_evidence:
        return None

    candidate_range = range(line_start, line_end + 1)
    for bundle in bundles:
        for chunk in bundle.candidate_chunks:
            for evidence in candidate.supporting_evidence:
                evidence_range = range(evidence.line_start, evidence.line_end + 1)
                if (
                    chunk.file_path == file_path
                    and evidence.file_path == file_path
                    and evidence.chunk_index == chunk.chunk_index
                    and chunk.line_start <= line_start
                    and chunk.line_end >= line_end
                    and chunk.line_start <= evidence.line_start
                    and chunk.line_end >= evidence.line_end
                    and _ranges_overlap(candidate_range, evidence_range)
                ):
                    return chunk
    return None


def _ranges_overlap(left: range, right: range) -> bool:
    return left.start <= right.stop - 1 and right.start <= left.stop - 1


def _candidate_has_required_fields(candidate: ProbeJudgeIssueCandidate) -> bool:
    return (
        bool(candidate.title)
        and bool(candidate.description)
        and _issue_category(candidate.category) is not None
        and _issue_severity(candidate.severity) is not None
        and isinstance(candidate.file_path, str)
        and isinstance(candidate.line_start, int)
        and isinstance(candidate.line_end, int)
        and candidate.line_start <= candidate.line_end
    )


def _candidate_rule_id(
    candidate: ProbeJudgeIssueCandidate,
    bundles: list[ProbeEvidenceBundle],
    *,
    roadmap_by_id: dict[str, RoadmapRequirement] | None = None,
) -> str | None:
    if candidate.rule_id:
        return candidate.rule_id
    if candidate.probe_id:
        for bundle in bundles:
            if _probe_id(bundle.probe) == candidate.probe_id:
                rule_ids = list(bundle.probe.related_rule_ids)
                return _matching_rule_id(candidate, rule_ids, roadmap_by_id)
    for bundle in bundles:
        rule_ids = list(bundle.probe.related_rule_ids)
        if rule_ids:
            return _matching_rule_id(candidate, rule_ids, roadmap_by_id)
    return None


def _matching_rule_id(
    candidate: ProbeJudgeIssueCandidate,
    rule_ids: list[str],
    roadmap_by_id: dict[str, RoadmapRequirement] | None,
) -> str | None:
    if not rule_ids:
        return None
    if len(rule_ids) == 1 or not roadmap_by_id:
        return rule_ids[0]

    candidate_text = _candidate_search_text(candidate)
    for rule_id in rule_ids:
        rule = roadmap_by_id.get(rule_id)
        if rule is not None and _rule_matches_candidate_text(rule, candidate_text):
            return rule_id

    return rule_ids[0]


def _candidate_search_text(candidate: ProbeJudgeIssueCandidate) -> str:
    values = [
        candidate.title,
        candidate.description,
        candidate.suggestion,
        candidate.rule_id,
        candidate.probe_id,
        *(evidence.rationale for evidence in candidate.supporting_evidence),
        *(evidence.rationale for evidence in candidate.contradicting_evidence),
    ]
    return " ".join(value.lower() for value in values if value)


def _rule_matches_candidate_text(
    rule: RoadmapRequirement,
    candidate_text: str,
) -> bool:
    for package_name in _target_package_names(rule):
        if _package_name_in_text(package_name, candidate_text):
            return True

    requirement_terms = [
        term
        for term in tokenize(rule.requirement.lower())
        if len(term) >= MIN_IMPORTANT_RULE_TERM_LENGTH and term not in STOP_WORDS
    ]
    return bool(requirement_terms) and all(
        term in candidate_text for term in requirement_terms[:3]
    )


def _dependency_manifest_contradicts_candidate(
    *,
    candidate: ProbeJudgeIssueCandidate,
    rule: RoadmapRequirement | None,
    evidence_chunk: ProbeCandidateChunk,
) -> bool:
    if rule is None or rule.check_type != "required_dependency":
        return False
    if not evidence_chunk.file_path.endswith("package.json"):
        return False

    manifest = _json_object(evidence_chunk.content)
    if not manifest:
        return False

    package_versions = _package_versions(manifest)
    for package_name in _target_package_names(rule):
        actual_version = package_versions.get(package_name.lower())
        if actual_version is None:
            continue

        version_constraint = _target_version_constraint(rule)
        if version_constraint and not _version_satisfies(
            actual_version,
            version_constraint,
        ):
            return False

        logger.info(
            "Rejecting roadmap dependency candidate contradicted by manifest: "
            "rule_id=%s package=%s file=%s line=%s",
            rule.rule_id,
            package_name,
            candidate.file_path,
            candidate.line_start,
        )
        return True

    return False


def _json_object(content: str) -> dict[str, object]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _package_versions(manifest: dict[str, object]) -> dict[str, str]:
    package_versions: dict[str, str] = {}
    for section_name in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        section = manifest.get(section_name)
        if not isinstance(section, dict):
            continue
        for package_name, version in section.items():
            if isinstance(package_name, str) and isinstance(version, str):
                package_versions[package_name.lower()] = version

    return package_versions


def _target_package_names(rule: RoadmapRequirement) -> list[str]:
    target = rule.target or {}
    package_any_of = target.get("package_any_of")
    if not isinstance(package_any_of, list):
        return []

    return [
        package_name for package_name in package_any_of if isinstance(package_name, str)
    ]


def _target_version_constraint(rule: RoadmapRequirement) -> str | None:
    target = rule.target or {}
    version_constraint = target.get("version_constraint")
    if not isinstance(version_constraint, str) or not version_constraint.strip():
        return None

    return version_constraint.strip()


def _version_satisfies(actual_version: str, constraint: str) -> bool:
    actual_major = _major_version(actual_version)
    constraint_major = _major_version(constraint)
    if actual_major is None or constraint_major is None:
        return True

    return actual_major == constraint_major


def _major_version(version_text: str) -> int | None:
    match = re.search(r"\d+", version_text)
    if match is None:
        return None

    return int(match.group(0))


def _package_name_in_text(package_name: str, text: str) -> bool:
    normalized_package = package_name.lower()
    if normalized_package in text:
        return True
    if normalized_package == "next":
        return any(alias in text for alias in ("next.js", "nextjs"))
    if normalized_package == "tailwindcss":
        return "tailwind" in text

    return False


def _candidate_references(
    *,
    category: IssueCategory,
    rule_id: str | None,
    roadmap_by_id: dict[str, RoadmapRequirement],
) -> list[str]:
    if rule_id is not None and rule_id in roadmap_by_id:
        return ["roadmap_rule_catalog", rule_id]
    if category == IssueCategory.SECURITY:
        return ["backend_directed_security_probe"]
    return ["backend_directed_probe"]


def _source_context(chunk: ProbeCandidateChunk) -> dict[str, object]:
    return {
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "start_line": chunk.line_start,
        "lines": chunk.content.splitlines(),
    }


def _probe_judge_response_from_payload(
    payload: dict[str, object],
) -> ProbeJudgeResponse:
    try:
        return ProbeJudgeResponse.model_validate(payload)
    except ValidationError as error:
        logger.warning("Probe judge returned invalid JSON schema: %s", error)

    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return ProbeJudgeResponse(schema_rejected_count=1)

    valid_candidates: list[ProbeJudgeIssueCandidate] = []
    schema_rejected_count = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            schema_rejected_count += 1
            continue
        try:
            valid_candidates.append(ProbeJudgeIssueCandidate.model_validate(candidate))
        except ValidationError:
            schema_rejected_count += 1

    if schema_rejected_count:
        logger.warning(
            "Probe judge kept %s schema-valid candidates and rejected %s malformed "
            "candidates.",
            len(valid_candidates),
            schema_rejected_count,
        )

    return ProbeJudgeResponse(
        candidates=valid_candidates,
        schema_rejected_count=schema_rejected_count,
    )


def _message_content(result: Any) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _json_object_from_text(text: str) -> dict[str, object]:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced is not None:
        stripped = fenced.group(1)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _issue_category(value: str | None) -> IssueCategory:
    if isinstance(value, str):
        normalized = value.strip().lower()
        for category in IssueCategory:
            if category.value == normalized:
                return category
    return IssueCategory.REQUIREMENT


def _issue_severity(value: str | None) -> IssueSeverity:
    if isinstance(value, str):
        normalized = value.strip().lower()
        for severity in IssueSeverity:
            if severity.value == normalized:
                return severity
    return IssueSeverity.MEDIUM


def _files_to_review(
    *,
    file_tree: list[object],
    chunk_counts: dict[str, int],
    chunk_risks: dict[str, set[str]],
) -> list[dict[str, object]]:
    file_paths = {
        str(entry["path"])
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    }
    file_paths.update(chunk_counts)
    return [
        {
            "file_path": file_path,
            "priority": _file_priority(file_path, chunk_risks.get(file_path, set())),
            "risk_area": _file_risk_area(file_path, chunk_risks.get(file_path, set())),
            "total_chunks": chunk_counts.get(file_path, 0),
        }
        for file_path in sorted(file_paths)
    ]


def _chunk_counts_by_file(chunk_documents: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for document in chunk_documents:
        file_path = document.get("file_path")
        total_chunks = document.get("total_chunks")
        chunk_index = document.get("chunk_index")
        if not isinstance(file_path, str):
            continue
        if isinstance(total_chunks, int):
            counts[file_path] = max(counts.get(file_path, 0), total_chunks)
        elif isinstance(chunk_index, int):
            counts[file_path] = max(counts.get(file_path, 0), chunk_index + 1)
    return counts


def _chunk_risks_by_file(
    chunk_documents: list[dict[str, Any]],
) -> dict[str, set[str]]:
    risks: dict[str, set[str]] = {}
    for document in chunk_documents:
        file_path = document.get("file_path")
        risk_area = document.get("risk_area")
        if isinstance(file_path, str) and isinstance(risk_area, str):
            risks.setdefault(file_path, set()).add(risk_area)
    return risks


def _file_priority(file_path: str, risk_areas: set[str]) -> str:
    if risk_areas & {"security", "api"}:
        return "high"
    if _path_category(file_path) == "security":
        return "high"
    if risk_areas & {"database", "config"}:
        return "medium"
    return "low"


def _file_risk_area(file_path: str, risk_areas: set[str]) -> str:
    if "security" in risk_areas or _path_category(file_path) == "security":
        return "security"
    if "database" in risk_areas:
        return "database"
    if "api" in risk_areas:
        return "api"
    if "config" in risk_areas:
        return "config"
    return "general"


def _path_category(file_path: str) -> str:
    path = PurePosixPath(file_path.replace("\\", "/"))
    parts = {part.lower() for part in path.parts}
    parts.add(path.stem.lower())
    if parts & {"auth", "security", "crypto", "middleware"}:
        return "security"
    return "general"


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _document_key(document: dict[str, Any]) -> str:
    return f"{document.get('file_path')}:{document.get('chunk_index')}"


def _probe_id(probe: ProbeDefinition) -> str:
    return probe.probe_id


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
