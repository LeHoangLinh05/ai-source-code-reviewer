"""Prompt and response helpers for backend-directed probe judging."""

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.ai.probe.contracts import (
    SENSITIVE_DATA_LOGGING_PROBE_ID,
    UNRESTRICTED_FILE_UPLOAD_PROBE_ID,
    ProbeLane,
)
from app.ai.probe.models import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssue,
    ProbeJudgeIssueCandidate,
    ProbeJudgeResponse,
    ProbeJudgeResult,
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
    SENSITIVE_DATA_LOGGING_PROBE_ID: IssueSeverity.HIGH,
    "security.sensitive_response_exposure": IssueSeverity.MEDIUM,
    UNRESTRICTED_FILE_UPLOAD_PROBE_ID: IssueSeverity.HIGH,
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
            "Return exactly one result for every probe. An issue result may contain "
            "multiple independent issues proved by the same evidence bundle. Use "
            "no_issue or uncertain with an empty issues array when evidence does "
            "not prove an issue. Preserve probe_id exactly and never return an "
            "unknown or duplicate probe_id. Use a stable snake_case claim_type for "
            "each issue. When allowed_claim_types is non-empty, every issue must use "
            "one of those exact claim types. "
            "IMPORTANT: Do NOT split one root-cause defect into multiple issues. "
            "If the same code location has one underlying problem (e.g. OTP uses "
            "non-cryptographic random), report it as ONE issue only, not multiple "
            "issues with different titles. Multiple issues are only for truly "
            "independent defects at different locations or different root causes. "
            "Return exactly one JSON object and no markdown. Persistable issues "
            "require confidence >= 0.7. A Pydantic request model only blocks "
            "unknown fields; it does not prevent over-posting when a sensitive "
            "field is declared in that model. When severity_policy is present, "
            "use that exact severity for an issue verdict."
        ),
        "format_rules": [
            "results must be an array with exactly one item per requested probe.",
            "issues must be an array and may contain multiple issue objects.",
            "issue verdict requires at least one issue object.",
            "no_issue and uncertain verdicts require an empty issues array.",
            "supporting_evidence and contradicting_evidence must be arrays.",
            "Use [] for empty evidence arrays; never use null or a string.",
            "Every evidence item must include file_path, chunk_index, line_start, "
            "and line_end.",
            "Each issue file_path and line range must be covered by retrieved "
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
    issue_schema = {
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
    }
    return {
        "results": [
            {
                "probe_id": "non-empty string",
                "verdict": "issue | no_issue | uncertain",
                "rationale": "string | null",
                "confidence": "number between 0 and 1 | null",
                "issues": [issue_schema],
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
    if "results" in payload:
        response = _response_from_results(payload.get("results"))
    elif "candidates" in payload:
        response = _response_from_legacy_candidates(payload.get("candidates"))
    else:
        response = ProbeJudgeResponse(
            schema_rejected_count=1,
            has_unscoped_schema_error=True,
        )
    if response.schema_rejected_count:
        logger.warning(
            "Probe judge kept %s schema-valid results and rejected %s malformed "
            "result or issue objects.",
            len(response.results),
            response.schema_rejected_count,
        )
    return response


def _response_from_results(value: object) -> ProbeJudgeResponse:
    if not isinstance(value, list):
        return ProbeJudgeResponse(
            schema_rejected_count=1,
            has_unscoped_schema_error=True,
        )

    results: list[ProbeJudgeResult] = []
    schema_rejected_count = 0
    has_unscoped_schema_error = False
    for result_payload in value:
        if not isinstance(result_payload, dict):
            schema_rejected_count += 1
            has_unscoped_schema_error = True
            continue
        parsed_result, rejected_count = _result_from_payload(result_payload)
        schema_rejected_count += rejected_count
        if parsed_result is not None:
            results.append(parsed_result)
        else:
            has_unscoped_schema_error = True
    return ProbeJudgeResponse(
        results=results,
        schema_rejected_count=schema_rejected_count,
        has_unscoped_schema_error=has_unscoped_schema_error,
    )


def _result_from_payload(
    payload: dict[str, object],
) -> tuple[ProbeJudgeResult | None, int]:
    raw_issues = payload.get("issues", [])
    if not isinstance(raw_issues, list):
        return None, 1

    issues: list[ProbeJudgeIssue] = []
    rejected_count = 0
    for issue_payload in raw_issues:
        if not isinstance(issue_payload, dict):
            rejected_count += 1
            continue
        try:
            issues.append(ProbeJudgeIssue.model_validate(issue_payload))
        except ValidationError:
            rejected_count += 1

    normalized_payload = dict(payload)
    normalized_payload["issues"] = issues
    try:
        return ProbeJudgeResult.model_validate(normalized_payload), rejected_count
    except ValidationError:
        return None, rejected_count + 1


def _response_from_legacy_candidates(value: object) -> ProbeJudgeResponse:
    if not isinstance(value, list):
        return ProbeJudgeResponse(
            schema_rejected_count=1,
            has_unscoped_schema_error=True,
        )

    results: list[ProbeJudgeResult] = []
    schema_rejected_count = 0
    has_unscoped_schema_error = False
    for candidate_payload in value:
        if not isinstance(candidate_payload, dict):
            schema_rejected_count += 1
            has_unscoped_schema_error = True
            continue
        try:
            candidate = ProbeJudgeIssueCandidate.model_validate(candidate_payload)
            results.append(_result_from_legacy_candidate(candidate))
        except ValidationError:
            schema_rejected_count += 1
            has_unscoped_schema_error = True
    return ProbeJudgeResponse(
        results=results,
        schema_rejected_count=schema_rejected_count,
        has_unscoped_schema_error=has_unscoped_schema_error,
    )


def _result_from_legacy_candidate(
    candidate: ProbeJudgeIssueCandidate,
) -> ProbeJudgeResult:
    issue = ProbeJudgeIssue.model_validate(
        candidate.model_dump(exclude={"probe_id", "verdict"})
    )
    return ProbeJudgeResult(
        probe_id=candidate.probe_id,
        verdict=candidate.verdict,
        confidence=candidate.confidence,
        issues=[issue] if candidate.verdict == "issue" else [],
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
