"""Prompt and response helpers for backend-directed probe judging."""

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.ai.probe.contracts import ProbeLane
from app.ai.probe.models import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssueCandidate,
    ProbeJudgeResponse,
)
from app.models.review_issue import IssueCategory, IssueSeverity

logger = logging.getLogger(__name__)
PROBE_SEVERITY_POLICY = {
    "bug.inventory_invariant": IssueSeverity.HIGH,
    "security.insecure_randomness": IssueSeverity.MEDIUM,
    "security.jwt_algorithm_allowlist": IssueSeverity.CRITICAL,
    "security.mass_assignment": IssueSeverity.HIGH,
    "security.open_redirect": IssueSeverity.MEDIUM,
    "security.role_authorization": IssueSeverity.HIGH,
    "security.sensitive_response_exposure": IssueSeverity.MEDIUM,
}


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
            "Return exactly one candidate for every probe; use no_issue or "
            "uncertain when evidence does not prove an issue. Preserve probe_id "
            "exactly and never return an unknown or duplicate probe_id. "
            "Return exactly one JSON object and no markdown. Persistable issues "
            "require confidence >= 0.7. A Pydantic request model only blocks "
            "unknown fields; it does not prevent over-posting when a sensitive "
            "field is declared in that model. When severity_policy is present, "
            "use that exact severity for an issue verdict."
        ),
        "format_rules": [
            "candidates must be an array.",
            "supporting_evidence and contradicting_evidence must be arrays.",
            "Use [] for empty evidence arrays; never use null or a string.",
            "Every evidence item must include file_path, chunk_index, line_start, "
            "and line_end.",
            "The candidate file_path and line range must be covered by retrieved "
            "source chunks and overlap at least one supporting evidence range.",
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
                "probe_id": "non-empty string",
            }
        ]
    }


def _bundle_for_prompt(bundle: ProbeEvidenceBundle) -> dict[str, object]:
    return {
        "probe": bundle.probe.prompt_payload(),
        "severity_policy": (
            severity.value
            if (severity := PROBE_SEVERITY_POLICY.get(bundle.probe.probe_id))
            else None
        ),
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


def _probe_issue_severity(probe_id: str, value: str | None) -> IssueSeverity:
    return PROBE_SEVERITY_POLICY.get(probe_id, _issue_severity(value))
