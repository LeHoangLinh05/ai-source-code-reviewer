"""Backend-directed probe retrieval and evidence-only issue judging."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import re
import time
from typing import Any, Protocol
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rag.bm25_index import BM25Document, BM25Index, tokenize
from app.ai.rag.code_retriever import CodeSemanticRetriever
from app.ai.roadmap.knowledge import RoadmapRequirement, load_roadmap_requirements
from app.ai.roadmap.selection import build_roadmap_context
from app.ai.semantic_audit_plan import build_semantic_audit_plan
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
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

    @property
    def key(self) -> tuple[str, int]:
        return self.file_path, self.chunk_index


@dataclass(slots=True, frozen=True)
class ProbeEvidenceBundle:
    """Retrieved evidence for one semantic audit probe."""

    probe: dict[str, object]
    retrieval_status: str
    candidate_chunks: list[ProbeCandidateChunk]
    strategies_used: list[str]


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
        max_chunks: int = 160,
    ) -> None:
        self.database = database
        self.code_retriever = code_retriever
        self.enable_semantic_search = enable_semantic_search
        self.chunks_per_probe = max(1, chunks_per_probe)
        self.max_chunks = max(1, max_chunks)

    async def retrieve(
        self,
        *,
        job_id: UUID,
        probes: list[dict[str, object]],
        trace_writer: SyntheticTraceWriter,
    ) -> list[ProbeEvidenceBundle]:
        """Run hybrid retrieval for every probe and write redacted traces."""

        chunk_documents = await _load_chunk_documents(self.database, job_id)
        bm25_index = _build_bm25_index(chunk_documents)
        bundles: list[ProbeEvidenceBundle] = []
        for probe in probes:
            started_at = time.perf_counter()
            bundle = await self._retrieve_probe(
                job_id=job_id,
                probe=probe,
                chunk_documents=chunk_documents,
                bm25_index=bm25_index,
            )
            bundles.append(bundle)
            await _write_probe_retrieval_trace(
                trace_writer=trace_writer,
                probe=probe,
                bundle=bundle,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )

        return _trim_bundles(bundles, max_chunks=self.max_chunks)

    async def _retrieve_probe(
        self,
        *,
        job_id: UUID,
        probe: dict[str, object],
        chunk_documents: list[dict[str, Any]],
        bm25_index: BM25Index,
    ) -> ProbeEvidenceBundle:
        query = str(probe.get("query") or "").strip()
        top_k = min(
            max(_optional_int(probe.get("top_k")) or self.chunks_per_probe, 1), 10
        )
        candidate_by_key: dict[tuple[str, int], ProbeCandidateChunk] = {}
        strategies: list[str] = []

        semantic_results = []
        if query and self.enable_semantic_search:
            try:
                semantic_results = await _semantic_search(
                    retriever=self._code_retriever(),
                    job_id=job_id,
                    query=query,
                    top_k=max(top_k * 4, self.chunks_per_probe),
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
                candidate_by_key[chunk.key] = chunk

        lexical_results = bm25_index.search(query, top_k=max(top_k * 8, 20))
        strategies.append("bm25")
        for result in lexical_results:
            chunk = _candidate_from_document(
                result.metadata,
                probe=probe,
                query=query,
                semantic_score=0.0,
                lexical_score=result.score,
            )
            if chunk is None:
                continue
            previous = candidate_by_key.get(chunk.key)
            if previous is None or chunk.final_score > previous.final_score:
                candidate_by_key[chunk.key] = chunk

        exact_candidates = _exact_candidates(
            chunk_documents=chunk_documents,
            probe=probe,
            query=query,
            top_k=max(top_k * 4, 12),
        )
        strategies.append("exact")
        for chunk in exact_candidates:
            previous = candidate_by_key.get(chunk.key)
            if previous is None or chunk.final_score > previous.final_score:
                candidate_by_key[chunk.key] = chunk

        selected = sorted(
            candidate_by_key.values(),
            key=lambda candidate: candidate.final_score,
            reverse=True,
        )[: self.chunks_per_probe]
        status = "ok" if selected else "no_candidate_evidence"
        return ProbeEvidenceBundle(
            probe=probe,
            retrieval_status=status,
            candidate_chunks=selected,
            strategies_used=strategies,
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

        assert candidate.file_path is not None
        assert candidate.line_start is not None
        assert candidate.line_end is not None
        evidence_chunk = _supporting_bundle_chunk(candidate, bundles)
        if evidence_chunk is None:
            return False

        category = _issue_category(candidate.category)
        severity = _issue_severity(candidate.severity)
        existing_issue = await self._find_existing_issue(
            job_id=job_id,
            file_path=candidate.file_path,
            line_start=candidate.line_start,
            category=category,
        )
        if existing_issue is not None:
            return False

        rule_id = _candidate_rule_id(candidate, bundles)
        issue_source = IssueSource.KB if rule_id else IssueSource.AI_REVIEW
        references = _candidate_references(
            category=category,
            rule_id=rule_id,
            roadmap_by_id=self.roadmap_by_id,
        )
        review_issue = ReviewIssue(
            job_id=job_id,
            file_path=candidate.file_path,
            line_start=candidate.line_start,
            line_end=candidate.line_end,
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
    max_probes_per_batch: int,
    max_chunks_per_batch: int,
) -> ProbeReviewResult:
    """Run the default backend-directed review path."""

    probes = await build_backend_probe_plan(
        job_id=job_id,
        postgres_session=postgres_session,
        database=mongodb_database,
    )
    retrieval_service = ProbeRetrievalService(
        database=mongodb_database,
        enable_semantic_search=enable_semantic_search,
        chunks_per_probe=chunks_per_probe,
        max_chunks=max_chunks,
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
) -> list[dict[str, object]]:
    """Build the same unified category probe plan without a tool loop."""

    structure_document = await database[FILE_ANALYSIS_RESULTS_COLLECTION].find_one(
        {"job_id": str(job_id)},
        sort=[("analyzed_at", -1)],
    )
    static_documents = (
        await database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION]
        .find({"job_id": str(job_id)})
        .to_list(length=None)
    )
    chunk_documents = await _load_chunk_documents(database, job_id)
    job_options = await _load_job_options(postgres_session, job_id)
    roadmap_context = _build_roadmap_context_without_vectorstore(job_options)
    file_tree = []
    if isinstance(structure_document, dict):
        file_tree = _as_list(structure_document.get("file_tree"))
    static_issues = _static_issues(static_documents)
    return build_semantic_audit_plan(
        roadmap_context=roadmap_context,
        files_to_review=_files_to_review(
            file_tree=file_tree,
            static_issues=static_issues,
            chunk_counts=_chunk_counts_by_file(chunk_documents),
        ),
        static_issues=static_issues,
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
    query: str,
    top_k: int,
) -> list[Any]:
    return await _to_thread_search(
        retriever,
        query=query,
        job_id=job_id,
        top_k=top_k,
    )


async def _to_thread_search(
    retriever: CodeSemanticRetriever,
    *,
    query: str,
    job_id: UUID,
    top_k: int,
) -> list[Any]:
    import asyncio

    return await asyncio.to_thread(
        retriever.search,
        query=query,
        job_id=job_id,
        top_k=top_k,
    )


def _candidate_from_semantic_result(
    result: Any,
    *,
    probe: dict[str, object],
    query: str,
) -> ProbeCandidateChunk | None:
    return _candidate_from_document(
        dict(result.metadata),
        probe=probe,
        query=query,
        semantic_score=float(result.semantic_score),
        lexical_score=0.0,
        content=str(result.content),
    )


def _candidate_from_document(
    document: dict[str, Any],
    *,
    probe: dict[str, object],
    query: str,
    semantic_score: float,
    lexical_score: float,
    content: str | None = None,
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
    static_score = 0.12 if document.get("has_static_issues") is True else 0.0
    risk_score = _risk_score(document, probe)
    exact_score = _exact_score(query, chunk_text, file_path)
    final_score = (
        semantic_score * 0.45
        + lexical_score * 0.25
        + exact_score * 0.2
        + path_score
        + static_score
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
    )


def _exact_candidates(
    *,
    chunk_documents: list[dict[str, Any]],
    probe: dict[str, object],
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
        )
        if candidate is not None:
            candidates.append(candidate)

    return sorted(candidates, key=lambda item: item.final_score, reverse=True)[:top_k]


def _exact_score(query: str, content: str, file_path: str) -> float:
    query_terms = _important_terms(query)
    if not query_terms:
        return 0.0
    searchable = f"{file_path}\n{content}".lower()
    matched = sum(1 for term in query_terms if term in searchable)
    return min(1.0, matched / max(len(query_terms), 1))


def _important_terms(query: str) -> list[str]:
    return [
        term for term in tokenize(query) if len(term) >= 3 and term not in STOP_WORDS
    ][:32]


def _path_score(
    *,
    file_path: str,
    probe: dict[str, object],
    query: str,
) -> float:
    normalized_path = file_path.replace("\\", "/").lower()
    category = str(probe.get("category") or probe.get("review_category") or "")
    hints = PATH_HINTS_BY_CATEGORY.get(category, ())
    score = 0.0
    if any(hint in normalized_path for hint in hints):
        score += 0.2
    if any(term in normalized_path for term in _important_terms(query)[:10]):
        score += 0.1
    if _is_non_runtime_path(normalized_path):
        score -= 0.45
    return score


def _risk_score(document: dict[str, Any], probe: dict[str, object]) -> float:
    chunk_risk = str(document.get("risk_area") or "")
    probe_risk = str(probe.get("risk_area") or "")
    category = str(probe.get("category") or probe.get("review_category") or "")
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
    remaining_slots = max_chunks
    for bundle in bundles:
        if remaining_slots <= 0 or not bundle.candidate_chunks:
            break
        if _is_high_priority_probe(bundle.probe):
            kept[_probe_id(bundle.probe)].add(bundle.candidate_chunks[0].key)
            remaining_slots -= 1

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
            replace(bundle, retrieval_status=status, candidate_chunks=chunks)
        )

    return trimmed


def _trim_rank(
    probe: dict[str, object],
    chunk: ProbeCandidateChunk,
) -> tuple[int, float]:
    category = str(probe.get("category") or "")
    priority = str(probe.get("priority") or "low")
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


def _is_high_priority_probe(probe: dict[str, object]) -> bool:
    return (
        str(probe.get("priority") or "") == "high"
        or str(probe.get("category") or "") == "security"
    )


def _judge_batches(
    bundles: list[ProbeEvidenceBundle],
    *,
    max_probes: int,
    max_chunks: int,
) -> list[list[ProbeEvidenceBundle]]:
    batches: list[list[ProbeEvidenceBundle]] = []
    current_batch: list[ProbeEvidenceBundle] = []
    current_chunk_count = 0
    for bundle in bundles:
        if not bundle.candidate_chunks:
            continue
        bundle_chunk_count = len(bundle.candidate_chunks)
        if current_batch and (
            len(current_batch) >= max_probes
            or current_chunk_count + bundle_chunk_count > max_chunks
        ):
            batches.append(current_batch)
            current_batch = []
            current_chunk_count = 0
        current_batch.append(bundle)
        current_chunk_count += bundle_chunk_count

    if current_batch:
        batches.append(current_batch)

    return batches


def _judge_prompt(batch: list[ProbeEvidenceBundle]) -> str:
    payload = {
        "instruction": (
            "For each probe, decide whether the evidence proves a real issue. "
            "Do not invent files, rules, or missing behavior outside the chunks. "
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
        "probe": bundle.probe,
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
    probe: dict[str, object],
    bundle: ProbeEvidenceBundle,
    duration_ms: int,
) -> None:
    await trace_writer.write_synthetic_tool_log(
        tool_name=PROBE_RETRIEVAL_TOOL_NAME,
        tool_input={
            "probe_id": _probe_id(probe),
            "category": str(probe.get("category") or ""),
            "related_rule_ids": _string_list(probe.get("related_rule_ids")),
            "query": str(probe.get("query") or ""),
        },
        output={
            "status": bundle.retrieval_status,
            "summary": "source content redacted from tool trace",
            "duration_ms": duration_ms,
            "strategies_used": bundle.strategies_used,
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
    assert candidate.file_path is not None
    assert candidate.line_start is not None
    assert candidate.line_end is not None
    for bundle in bundles:
        for chunk in bundle.candidate_chunks:
            if (
                chunk.file_path == candidate.file_path
                and chunk.line_start <= candidate.line_start
                and chunk.line_end >= candidate.line_end
            ):
                return chunk
    return None


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
) -> str | None:
    if candidate.rule_id:
        return candidate.rule_id
    if candidate.probe_id:
        for bundle in bundles:
            if _probe_id(bundle.probe) == candidate.probe_id:
                rule_ids = _string_list(bundle.probe.get("related_rule_ids"))
                return rule_ids[0] if rule_ids else None
    for bundle in bundles:
        rule_ids = _string_list(bundle.probe.get("related_rule_ids"))
        if rule_ids:
            return rule_ids[0]
    return None


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


def _static_issues(static_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for document in static_documents:
        tool_name = str(document.get("tool", "static"))
        for issue in _as_list(document.get("parsed_issues")):
            if not isinstance(issue, dict):
                continue
            normalized_issue = dict(issue)
            normalized_issue["source"] = tool_name
            issues.append(normalized_issue)
    return issues


def _files_to_review(
    *,
    file_tree: list[object],
    static_issues: list[dict[str, Any]],
    chunk_counts: dict[str, int],
) -> list[dict[str, object]]:
    issue_by_file: dict[str, list[dict[str, Any]]] = {}
    for issue in static_issues:
        file_path = issue.get("file_path")
        if file_path:
            issue_by_file.setdefault(str(file_path), []).append(issue)
    file_paths = {
        str(entry["path"])
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    }
    file_paths.update(issue_by_file)
    file_paths.update(chunk_counts)
    return [
        {
            "file_path": file_path,
            "priority": _file_priority(file_path, issue_by_file.get(file_path, [])),
            "risk_area": _file_risk_area(file_path, issue_by_file.get(file_path, [])),
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


def _file_priority(file_path: str, issues: list[dict[str, Any]]) -> str:
    severities = {str(issue.get("severity")) for issue in issues}
    categories = {str(issue.get("category")) for issue in issues}
    if severities & {"critical", "high"} or categories & {"security", "bug"}:
        return "high"
    if _path_category(file_path) == "security":
        return "high"
    if issues:
        return "medium"
    return "low"


def _file_risk_area(file_path: str, issues: list[dict[str, Any]]) -> str:
    categories = {str(issue.get("category")) for issue in issues}
    if "security" in categories or _path_category(file_path) == "security":
        return "security"
    if "performance" in categories:
        return "performance"
    if "bug" in categories:
        return "bug"
    return "maintainability" if issues else "general"


def _path_category(file_path: str) -> str:
    parts = {part.lower() for part in file_path.replace("\\", "/").split("/")}
    if parts & {"auth", "security", "crypto", "middleware"}:
        return "security"
    return "general"


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _document_key(document: dict[str, Any]) -> str:
    return f"{document.get('file_path')}:{document.get('chunk_index')}"


def _probe_id(probe: dict[str, object]) -> str:
    return str(probe.get("probe_id") or probe.get("audit_plan_item_id") or "probe")


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
