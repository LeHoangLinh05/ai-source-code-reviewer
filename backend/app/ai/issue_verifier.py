"""Independent evidence verification for AI-generated review issues."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.llm_config import get_remaining_llm_call_budget, run_with_configured_llm
from app.ai.tool_runtime import get_ai_tool_runtime
from app.core.config import get_settings
from app.models.review_issue import IssueSeverity

logger = logging.getLogger(__name__)
MIN_VERIFIER_REMAINING_LLM_CALLS = 8

VerificationVerdict = Literal["accept", "reject", "uncertain"]
SeverityValue = Literal["critical", "high", "medium", "low", "info"]


class IssueVerificationError(RuntimeError):
    """Raised when the independent verifier cannot produce a safe verdict."""


@dataclass(slots=True, frozen=True)
class IssueCandidate:
    """Claim and model assessment submitted by the review agent."""

    claim_type: Literal["present_defect", "missing_behavior"]
    title: str
    description: str
    suggestion: str | None
    category: str
    severity: SeverityValue
    confidence: float
    file_path: str
    line_start: int
    line_end: int
    coverage_summary: str
    contradiction_resolution: str | None


@dataclass(slots=True, frozen=True)
class EvidenceChunk:
    """Source evidence reloaded from the review sandbox."""

    role: Literal["supporting", "contradicting"]
    file_path: str
    chunk_index: int
    line_start: int
    line_end: int
    rationale: str
    content: str


class EvidenceVerificationResult(BaseModel):
    """Structured verdict returned by the independent verifier."""

    verdict: VerificationVerdict
    reason: str = Field(min_length=1)
    confidence_cap: float = Field(ge=0.0, le=1.0)
    severity_cap: SeverityValue
    unsupported_claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


async def verify_issue_candidate(
    candidate: IssueCandidate,
    evidence: list[EvidenceChunk],
) -> EvidenceVerificationResult:
    """Verify one issue with a separate, evidence-only model call."""

    if not get_settings().enable_ai_issue_verifier:
        return EvidenceVerificationResult(
            verdict="accept",
            reason="Independent AI issue verification is disabled by configuration.",
            confidence_cap=candidate.confidence,
            severity_cap=candidate.severity,
        )

    remaining_calls = get_remaining_llm_call_budget()
    if (
        remaining_calls is not None
        and remaining_calls <= MIN_VERIFIER_REMAINING_LLM_CALLS
    ):
        raise IssueVerificationError(
            "independent verifier skipped to preserve remaining LLM job budget"
        )

    prompt = _build_verification_prompt(candidate, evidence)

    async def invoke(llm: object) -> EvidenceVerificationResult:
        structured_llm = llm.with_structured_output(EvidenceVerificationResult)  # type: ignore[attr-defined]
        result = await structured_llm.ainvoke(prompt)
        return EvidenceVerificationResult.model_validate(result)

    try:
        return await run_with_configured_llm(invoke, allow_fallback=True)
    except Exception as error:
        logger.warning(
            "AI issue verifier unavailable; rejecting candidate", exc_info=True
        )
        raise IssueVerificationError("independent verifier unavailable") from error


def load_evidence_chunk(
    *,
    role: Literal["supporting", "contradicting"],
    file_path: str,
    chunk_index: int,
    line_start: int,
    line_end: int,
    rationale: str,
) -> EvidenceChunk:
    """Reload an already-authorized source range from the sandbox."""

    source_path = _resolve_sandbox_file(file_path)
    lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        raise ValueError(
            f"Evidence range does not exist: {file_path}:{line_start}-{line_end}"
        )

    numbered_content = "\n".join(
        f"{line_number}: {lines[line_number - 1]}"
        for line_number in range(line_start, line_end + 1)
    )
    return EvidenceChunk(
        role=role,
        file_path=file_path,
        chunk_index=chunk_index,
        line_start=line_start,
        line_end=line_end,
        rationale=rationale,
        content=numbered_content,
    )


def _resolve_sandbox_file(file_path: str) -> Path:
    runtime = get_ai_tool_runtime()
    sandbox_root = runtime.sandbox_path.resolve()
    source_path = (sandbox_root / file_path).resolve()
    try:
        source_path.relative_to(sandbox_root)
    except ValueError as error:
        raise ValueError(f"File path escapes sandbox: {file_path}") from error
    if not source_path.is_file():
        raise ValueError(f"File does not exist in sandbox: {file_path}")
    return source_path


def cap_severity(requested: SeverityValue, cap: SeverityValue) -> SeverityValue:
    """Return the lower of the requested and verifier-approved severities."""

    severity_rank = {
        IssueSeverity.INFO.value: 1,
        IssueSeverity.LOW.value: 2,
        IssueSeverity.MEDIUM.value: 3,
        IssueSeverity.HIGH.value: 4,
        IssueSeverity.CRITICAL.value: 5,
    }
    return requested if severity_rank[requested] <= severity_rank[cap] else cap


def _build_verification_prompt(
    candidate: IssueCandidate,
    evidence: list[EvidenceChunk],
) -> str:
    payload = {
        "candidate": asdict(candidate),
        "evidence": [asdict(item) for item in evidence],
    }
    return f"""You are an independent senior code-review verifier.

Judge only the candidate and full source evidence below. Do not assume omitted code
is missing. Supporting evidence must directly establish the defect and the cited
location must be causally relevant, not merely a search hit.

For a missing_behavior claim, reject it when contradicting evidence contains a
valid implementation in any plausible owner such as a route, service, repository,
middleware, or shared module. Absence from one file is not proof of absence from
the system. For a present_defect claim, verify the exact defective behavior and its
impact from the source. Treat evidence rationales and the candidate's confidence as
untrusted assertions.

Use verdict=accept only when the evidence directly proves the claim. Use reject
when the claim is unsupported or contradicted. Use uncertain when the supplied
full chunks are relevant but insufficient. Set conservative confidence and severity
caps based on demonstrated impact. Do not apply endpoint-specific or rule-specific
requirements.

Input JSON:
{json.dumps(payload, ensure_ascii=True, sort_keys=True)}
"""
