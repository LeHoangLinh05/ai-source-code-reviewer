"""Shared data contracts for backend-directed probe review."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from app.ai.probe.contracts import ProbeDefinition

ProbeBatchProgressCallback = Callable[[int, int], Awaitable[None]]


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


@dataclass(slots=True, frozen=True)
class ProbeReviewConfig:
    """Runtime limits for backend-directed probe retrieval and judging."""

    enable_semantic_search: bool
    max_chunks: int
    defect_max_chunks: int
    coverage_max_chunks: int
    roadmap_max_chunks: int
    max_probes_per_batch: int
    max_chunks_per_batch: int


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


def _probe_id(probe: ProbeDefinition) -> str:
    return probe.probe_id
