"""Shared data contracts for backend-directed probe review."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

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
    reported_issues: int
    created_issues: int
    rejected_issues: int
    no_issue_results: int
    uncertain_results: int
    handoff: str


@dataclass(slots=True, frozen=True)
class ProbeJudgeSummary:
    """Typed counters returned by judge execution and persistence."""

    judged_batches: int = 0
    reported_issues: int = 0
    created_issues: int = 0
    rejected_issues: int = 0
    no_issue_results: int = 0
    uncertain_results: int = 0


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
    max_concurrency: int = 1


class ProbeJudgeEvidenceReference(BaseModel):
    """Chunk evidence reference returned by the probe judge."""

    file_path: str
    chunk_index: int
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    rationale: str | None = None


class ProbeJudgeIssue(BaseModel):
    """One concrete issue contained in a probe judge result."""

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


class ProbeJudgeResult(BaseModel):
    """Exactly one verdict for a requested probe, with zero or more issues."""

    probe_id: str = Field(min_length=1)
    verdict: Literal["issue", "no_issue", "uncertain"]
    rationale: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    issues: list[ProbeJudgeIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_issue_cardinality(self) -> ProbeJudgeResult:
        if self.verdict == "issue" and not self.issues:
            raise ValueError("issue verdict requires at least one issue")
        if self.verdict != "issue" and self.issues:
            raise ValueError(
                "no_issue and uncertain verdicts require an empty issue list"
            )
        return self


class ProbeJudgeIssueCandidate(ProbeJudgeIssue):
    """Legacy flattened candidate used by evidence validation adapters."""

    verdict: Literal["issue", "no_issue", "uncertain"]
    probe_id: str = Field(min_length=1)


class ProbeJudgeResponse(BaseModel):
    """Root JSON object expected from the LLM judge."""

    results: list[ProbeJudgeResult] = Field(default_factory=list)
    schema_rejected_count: int = 0
    has_unscoped_schema_error: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_candidates(cls, value: object) -> object:
        if not isinstance(value, dict) or "results" in value:
            return value
        candidates = value.get("candidates")
        if not isinstance(candidates, list):
            return value
        normalized = dict(value)
        normalized.pop("candidates", None)
        normalized["results"] = [
            (
                _result_payload_from_legacy_candidate(candidate)
                if isinstance(candidate, dict)
                else candidate
            )
            for candidate in candidates
        ]
        return normalized

    @property
    def candidates(self) -> list[ProbeJudgeIssueCandidate]:
        """Return the legacy flattened view for compatibility-only consumers."""

        return [
            candidate
            for result in self.results
            for candidate in _legacy_candidates_from_result(result)
        ]


def _result_payload_from_legacy_candidate(
    candidate: dict[str, object],
) -> dict[str, object]:
    verdict = candidate.get("verdict")
    issue_payload = {
        key: value
        for key, value in candidate.items()
        if key not in {"verdict", "probe_id"}
    }
    return {
        "probe_id": candidate.get("probe_id"),
        "verdict": verdict,
        "confidence": candidate.get("confidence"),
        "issues": [issue_payload] if verdict == "issue" else [],
    }


def _legacy_candidates_from_result(
    result: ProbeJudgeResult,
) -> list[ProbeJudgeIssueCandidate]:
    if result.verdict != "issue":
        return [
            ProbeJudgeIssueCandidate(
                probe_id=result.probe_id,
                verdict=result.verdict,
                confidence=result.confidence,
            )
        ]
    return [
        ProbeJudgeIssueCandidate(
            **issue.model_dump(),
            probe_id=result.probe_id,
            verdict=result.verdict,
        )
        for issue in result.issues
    ]


def _probe_id(probe: ProbeDefinition) -> str:
    return probe.probe_id
