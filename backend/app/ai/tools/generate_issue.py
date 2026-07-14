"""AI tool for producing normalized review issues."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Literal, cast

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.ai.source_evidence import (
    SOURCE_TOOL_NAMES,
    SourceChunkEvidence,
    line_range_is_covered,
    source_chunk_evidence,
)
from app.ai.issue_verifier import (
    EvidenceVerificationResult,
    IssueCandidate,
    IssueVerificationError,
    SeverityValue,
    cap_severity,
    load_evidence_chunk,
    verify_issue_candidate,
)
from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
from app.db.mongodb import TOOL_CALL_LOGS_COLLECTION
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)

SEVERITY_RANK = {
    IssueSeverity.CRITICAL: 5,
    IssueSeverity.HIGH: 4,
    IssueSeverity.MEDIUM: 3,
    IssueSeverity.LOW: 2,
    IssueSeverity.INFO: 1,
}
MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES = 0.75
MIN_VERIFIED_ISSUE_CONFIDENCE = 0.7
NON_RUNTIME_EVIDENCE_DIRS = frozenset(
    {
        "docs",
        "documentation",
        "examples",
        "fixtures",
        "spec",
        "specs",
        "test",
        "tests",
    }
)
RUNTIME_SOURCE_DIRS = frozenset(
    {
        "api",
        "app",
        "controllers",
        "core",
        "lib",
        "models",
        "repositories",
        "routes",
        "routers",
        "services",
        "src",
    }
)

logger = logging.getLogger(__name__)

AllowedIssueCategory = Literal[
    "security",
    "bug",
    "performance",
    "maintainability",
    "style",
    "requirement",
]
AllowedIssueSeverity = Literal["critical", "high", "medium", "low", "info"]
AllowedIssueSource = Literal["ai_review", "KB"]
AllowedClaimType = Literal["present_defect", "missing_behavior"]


class IssueValidationError(ValueError):
    """Raised when an AI issue violates backend safety gates."""


class SourceEvidenceReference(BaseModel):
    """Reference to a full source chunk previously delivered to the model."""

    tool_sequence: int | None = None
    result_index: int | None = None
    file_path: str
    chunk_index: int
    line_start: int
    line_end: int
    rationale: str = Field(min_length=1)


class GenerateIssueInput(BaseModel):
    """Input schema for normalized AI issues."""

    severity: str | None = None
    category: str | None = None
    title: str | None = None
    description: str | None = None
    confidence: float | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    references: list[str] | None = None
    source: str | None = None
    investigation_id: str | None = None
    claim_type: str | None = None
    supporting_evidence: list[SourceEvidenceReference] | None = None
    contradicting_evidence: list[SourceEvidenceReference] | None = None
    contradiction_resolution: str | None = None
    coverage_summary: str | None = None
    rule_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        data = unwrap_react_json_input(data, "input")
        data = unwrap_react_json_input(data, "severity")
        return _normalize_evidence_aliases(_normalize_location_aliases(data))


@tool(args_schema=GenerateIssueInput)
async def generate_issue(
    severity: str | None = None,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    confidence: float | None = None,
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    suggestion: str | None = None,
    references: list[str] | None = None,
    source: str | None = None,
    investigation_id: str | None = None,
    claim_type: str | None = None,
    supporting_evidence: list[SourceEvidenceReference] | None = None,
    contradicting_evidence: list[SourceEvidenceReference] | None = None,
    contradiction_resolution: str | None = None,
    coverage_summary: str | None = None,
    rule_id: str | None = None,
) -> dict[str, object]:
    """Tạo 1 issue có cấu trúc. CHỈ gọi khi confidence >= 0.7. Đây là cách DUY NHẤT để báo issue — không bao giờ trả issue dưới dạng free text."""

    parsed_input = parse_json_object_text(severity)
    if parsed_input is not None:
        parsed_input = cast(
            dict[str, object],
            _normalize_location_aliases(parsed_input),
        )
        severity = _optional_str(parsed_input.get("severity")) or severity
        category = _optional_category(parsed_input.get("category")) or category
        title = _optional_str(parsed_input.get("title")) or title
        description = _optional_str(parsed_input.get("description")) or description
        confidence = _optional_float(parsed_input.get("confidence")) or confidence
        file_path = _optional_str(parsed_input.get("file_path")) or file_path
        line_start = _optional_int(parsed_input.get("line_start")) or line_start
        line_end = _optional_int(parsed_input.get("line_end")) or line_end
        suggestion = _optional_str(parsed_input.get("suggestion")) or suggestion
        references = _optional_str_list(parsed_input.get("references")) or references
        source = _optional_source(parsed_input.get("source")) or source
        investigation_id = (
            _optional_str(parsed_input.get("investigation_id")) or investigation_id
        )
        claim_type = _optional_claim_type(parsed_input.get("claim_type")) or claim_type
        supporting_evidence = (
            _evidence_references(parsed_input.get("supporting_evidence"))
            or supporting_evidence
        )
        contradicting_evidence = (
            _evidence_references(parsed_input.get("contradicting_evidence"))
            or contradicting_evidence
        )
        contradiction_resolution = (
            _optional_str(parsed_input.get("contradiction_resolution"))
            or contradiction_resolution
        )
        coverage_summary = (
            _optional_str(parsed_input.get("coverage_summary")) or coverage_summary
        )
        rule_id = _optional_str(parsed_input.get("rule_id")) or rule_id

    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()

    references = references or []
    supporting_evidence = _evidence_references(supporting_evidence) or []
    contradicting_evidence = _evidence_references(contradicting_evidence) or []
    investigation_id = _optional_str(investigation_id)
    claim_type = _optional_claim_type(claim_type)
    coverage_summary = _optional_str(coverage_summary)
    category = _optional_category(category)
    severity = _optional_severity(severity)
    file_path = _normalize_issue_file_path(file_path)
    issue_source = _optional_source(source) or IssueSource.AI_REVIEW.value
    knowledge_metadata: dict[str, object] = {}
    if issue_source == IssueSource.KB.value:
        knowledge_references, knowledge_metadata = await _latest_kb_grounding(rule_id)
        if not knowledge_metadata:
            return _build_rejected_issue_response(
                "AI issue rejected: source=KB requires a successful "
                "search_knowledge_base result"
            )
        references = references or knowledge_references
        title = _sanitize_kb_visible_text(title)
        description = _sanitize_kb_visible_text(description)
        suggestion = _sanitize_kb_visible_text(suggestion)
    if category == IssueCategory.SECURITY.value and not references:
        references = await _latest_rag_references()
    confidence = _adjust_security_confidence(
        category=category,
        confidence=confidence,
        references=references,
    )

    investigation_rejection = _investigation_fields_rejection_reason(
        investigation_id=investigation_id,
        claim_type=claim_type,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        contradiction_resolution=contradiction_resolution,
        coverage_summary=coverage_summary,
    )
    if investigation_rejection is not None:
        return _build_rejected_issue_response(investigation_rejection)

    rejection_reason = _issue_rejection_reason(
        category=category,
        confidence=confidence,
        description=description,
        severity=severity,
        title=title,
    )
    if rejection_reason is not None:
        existing_evidence_issue = await _find_existing_issue_from_supporting_evidence(
            supporting_evidence=supporting_evidence,
        )
        if existing_evidence_issue is not None:
            return {
                "status": "skipped",
                "reason": "duplicate_ai_review_issue_from_supporting_evidence",
                "issue_id": str(existing_evidence_issue.id),
                "source": existing_evidence_issue.source.value,
            }
        return _build_rejected_issue_response(rejection_reason)

    assert category is not None
    assert confidence is not None
    assert description is not None
    assert severity is not None
    assert title is not None
    assert investigation_id is not None
    assert claim_type is not None
    assert coverage_summary is not None

    evidence_rejection = await _evidence_rejection_reason(
        investigation_id=investigation_id,
        claim_type=claim_type,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
    )
    if evidence_rejection is not None:
        return _build_rejected_issue_response(evidence_rejection)

    owner_rejection = _behavior_evidence_owner_rejection_reason(
        category=category,
        supporting_evidence=supporting_evidence,
    )
    if owner_rejection is not None:
        return _build_rejected_issue_response(owner_rejection)

    try:
        validate_issue_payload(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            severity=severity,
            category=category,
            title=title,
            description=description,
            suggestion=suggestion,
            confidence=confidence,
            references=references,
            source=issue_source,
            knowledge_metadata=knowledge_metadata,
        )
    except IssueValidationError as error:
        return _build_rejected_issue_response(str(error))

    if (
        file_path is not None
        and line_start is not None
        and line_end is not None
        and not await _has_source_evidence(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        )
    ):
        return _build_rejected_issue_response(
            "AI issue rejected: source range was not returned by read_file_chunk"
        )

    issue_fingerprint = _issue_evidence_fingerprint(
        investigation_id=investigation_id,
        claim_type=claim_type,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
    )
    if runtime.confidence_increase_without_new_evidence(
        fingerprint=issue_fingerprint,
        confidence=confidence,
    ):
        return _build_rejected_issue_response(
            "AI issue rejected: confidence cannot increase without new evidence"
        )

    assert file_path is not None
    assert line_start is not None
    assert line_end is not None
    verification = await _verify_issue(
        claim_type=claim_type,
        title=title,
        description=description,
        suggestion=suggestion,
        category=category,
        severity=severity,
        confidence=confidence,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        coverage_summary=coverage_summary,
        contradiction_resolution=contradiction_resolution,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
    )
    if verification.verdict != "accept":
        return _build_verifier_rejection_response(verification)

    confidence = min(confidence, verification.confidence_cap)
    if confidence < MIN_VERIFIED_ISSUE_CONFIDENCE:
        return _build_rejected_issue_response(
            "AI issue rejected: verifier confidence cap is below persistence threshold"
        )
    severity = cap_severity(
        cast(SeverityValue, severity),
        verification.severity_cap,
    )

    existing_issue = await _find_existing_issue(
        file_path=file_path,
        line_start=line_start,
        category=IssueCategory(category),
    )
    if existing_issue is not None:
        if existing_issue.source in {IssueSource.AI_REVIEW, IssueSource.KB}:
            return {
                "status": "skipped",
                "reason": "duplicate_ai_review_issue",
                "source": existing_issue.source.value,
            }
        if (
            SEVERITY_RANK[existing_issue.severity]
            > SEVERITY_RANK[IssueSeverity(severity)]
        ):
            severity = existing_issue.severity.value
    severity = cap_severity(
        cast(SeverityValue, severity),
        verification.severity_cap,
    )

    raw_output: dict[str, object] = {"references": references}
    raw_output.update(knowledge_metadata)
    raw_output["verification"] = verification.model_dump(mode="json")
    raw_output["source_context"] = _build_source_context(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )
    raw_output["investigation"] = {
        "investigation_id": investigation_id,
        "claim_type": claim_type,
        "supporting_evidence": [
            reference.model_dump(mode="json") for reference in supporting_evidence
        ],
        "contradicting_evidence": [
            reference.model_dump(mode="json") for reference in contradicting_evidence
        ],
        "contradiction_resolution": contradiction_resolution,
        "coverage_summary": coverage_summary,
        "evidence_fingerprint": issue_fingerprint,
    }
    review_issue = ReviewIssue(
        job_id=runtime.job_id,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        severity=IssueSeverity(severity),
        category=IssueCategory(category),
        title=title[:255],
        description=description,
        suggestion=suggestion,
        source=IssueSource(issue_source),
        confidence=confidence,
        raw_output=raw_output,
    )
    runtime.postgres_session.add(review_issue)
    await runtime.postgres_session.commit()
    await runtime.postgres_session.refresh(review_issue)
    return {
        "status": "created",
        "issue_id": str(review_issue.id),
        "source": issue_source,
    }


async def _verify_issue(
    *,
    claim_type: AllowedClaimType,
    title: str,
    description: str,
    suggestion: str | None,
    category: AllowedIssueCategory,
    severity: AllowedIssueSeverity,
    confidence: float,
    file_path: str,
    line_start: int,
    line_end: int,
    coverage_summary: str,
    contradiction_resolution: str | None,
    supporting_evidence: list[SourceEvidenceReference],
    contradicting_evidence: list[SourceEvidenceReference],
) -> EvidenceVerificationResult:
    candidate = IssueCandidate(
        claim_type=claim_type,
        title=title,
        description=description,
        suggestion=suggestion,
        category=category,
        severity=cast(SeverityValue, severity),
        confidence=confidence,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        coverage_summary=coverage_summary,
        contradiction_resolution=contradiction_resolution,
    )
    evidence = [
        load_evidence_chunk(
            role="supporting",
            file_path=reference.file_path,
            chunk_index=reference.chunk_index,
            line_start=reference.line_start,
            line_end=reference.line_end,
            rationale=reference.rationale,
        )
        for reference in supporting_evidence
    ]
    evidence.extend(
        load_evidence_chunk(
            role="contradicting",
            file_path=reference.file_path,
            chunk_index=reference.chunk_index,
            line_start=reference.line_start,
            line_end=reference.line_end,
            rationale=reference.rationale,
        )
        for reference in contradicting_evidence
    )
    try:
        return await verify_issue_candidate(candidate, evidence)
    except (IssueVerificationError, ValueError) as error:
        logger.warning("AI issue rejected because verifier failed: %s", error)
        return EvidenceVerificationResult(
            verdict="uncertain",
            reason=str(error),
            confidence_cap=0.0,
            severity_cap="info",
            unsupported_claims=["Candidate could not be independently verified."],
        )


def _build_verifier_rejection_response(
    verification: EvidenceVerificationResult,
) -> dict[str, object]:
    return {
        "status": "rejected",
        "reason": f"AI issue rejected by independent verifier: {verification.reason}",
        "verification": verification.model_dump(mode="json"),
    }


def _optional_category(value: object) -> AllowedIssueCategory | None:
    allowed_categories = {
        "security",
        "bug",
        "performance",
        "maintainability",
        "style",
        "requirement",
    }
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in allowed_categories:
            return cast(AllowedIssueCategory, normalized_value)

    return None


def _normalize_location_aliases(data: object) -> object:
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    if "file_path" not in normalized and isinstance(normalized.get("path"), str):
        normalized["file_path"] = normalized["path"]

    line_range = normalized.get("line_range")
    if isinstance(line_range, dict):
        if "line_start" not in normalized:
            normalized["line_start"] = line_range.get("start")
        if "line_end" not in normalized:
            normalized["line_end"] = line_range.get("end")
    elif isinstance(line_range, str):
        line_match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", line_range)
        if line_match is not None:
            normalized.setdefault("line_start", int(line_match.group(1)))
            normalized.setdefault("line_end", int(line_match.group(2)))

    return normalized


def _normalize_evidence_aliases(data: object) -> object:
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    for field_name in ("supporting_evidence", "contradicting_evidence"):
        value = normalized.get(field_name)
        if value == "":
            normalized[field_name] = []
            continue
        if not isinstance(value, list):
            continue

        normalized_items: list[object] = []
        for item in value:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            description = normalized_item.get("description")
            if "rationale" not in normalized_item and isinstance(description, str):
                normalized_item["rationale"] = description
            normalized_items.append(normalized_item)
        normalized[field_name] = normalized_items

    return normalized


def _sanitize_kb_visible_text(value: str | None) -> str | None:
    if value is None:
        return None

    sanitized = re.sub(
        r"\s*\([^)]*\brule\s*id\s*:[^)]*\)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(r"\bRC-[A-Z0-9-]+\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\broadmap\b", "requirements", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    return sanitized.strip()


def _optional_source(value: object) -> AllowedIssueSource | None:
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    if normalized_value.lower() == "ai_review":
        return "ai_review"
    if normalized_value.upper() == "KB":
        return "KB"
    return None


def _optional_claim_type(value: object) -> AllowedClaimType | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"present_defect", "missing_behavior"}:
        return cast(AllowedClaimType, normalized)
    return None


def _evidence_references(value: object) -> list[SourceEvidenceReference] | None:
    if not isinstance(value, list):
        return None
    references: list[SourceEvidenceReference] = []
    for item in value:
        if isinstance(item, SourceEvidenceReference):
            references.append(item)
        elif isinstance(item, dict):
            references.append(SourceEvidenceReference.model_validate(item))
    return references


def _normalize_issue_file_path(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or None


def _optional_severity(value: object) -> AllowedIssueSeverity | None:
    allowed_severities = {"critical", "high", "medium", "low", "info"}
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        priority_severity = {
            "p0": "critical",
            "p1": "high",
            "p2": "low",
        }.get(normalized_value)
        if priority_severity is not None:
            return cast(AllowedIssueSeverity, priority_severity)
        if normalized_value in allowed_severities:
            return cast(AllowedIssueSeverity, normalized_value)

    return None


def _issue_rejection_reason(
    *,
    category: AllowedIssueCategory | None,
    confidence: float | None,
    description: str | None,
    severity: AllowedIssueSeverity | None,
    title: str | None,
) -> str | None:
    missing_fields: list[str] = []
    if severity is None:
        missing_fields.append("severity")
    if category is None:
        missing_fields.append("category")
    if title is None:
        missing_fields.append("title")
    if description is None:
        missing_fields.append("description")
    if confidence is None:
        missing_fields.append("confidence")

    if not missing_fields:
        return None

    return f"AI issue rejected: missing or invalid fields: {', '.join(missing_fields)}"


def _investigation_fields_rejection_reason(
    *,
    investigation_id: str | None,
    claim_type: AllowedClaimType | None,
    supporting_evidence: list[SourceEvidenceReference],
    contradicting_evidence: list[SourceEvidenceReference],
    contradiction_resolution: str | None,
    coverage_summary: str | None,
) -> str | None:
    missing: list[str] = []
    if investigation_id is None:
        missing.append("investigation_id")
    if claim_type is None:
        missing.append("claim_type")
    if not supporting_evidence:
        missing.append("supporting_evidence")
    if coverage_summary is None:
        missing.append("coverage_summary")
    if contradicting_evidence and not contradiction_resolution:
        missing.append("contradiction_resolution")
    if not missing:
        return None
    return f"AI issue rejected: missing investigation fields: {', '.join(missing)}"


def _build_rejected_issue_response(reason: str) -> dict[str, object]:
    response: dict[str, object] = {"status": "rejected", "reason": reason}
    if "missing or invalid fields" in reason:
        response["next_action"] = (
            "Retry generate_issue with severity, category, title, description, "
            "confidence, and source line fields when file_path is present."
        )
    elif "evidence reference was not returned as full source" in reason:
        response["next_action"] = (
            "Call read_file_chunk for every missing evidence chunk, then retry "
            "generate_issue using only full-source chunks."
        )
    elif "claimed source range is not supporting evidence" in reason:
        response["next_action"] = (
            "Retry generate_issue with one contiguous file_path, line_start, and "
            "line_end from the covered supporting ranges in the rejection reason. "
            "Create separate issues for disjoint ranges."
        )
    elif (
        "File does not exist in sandbox" in reason
        or "file_path, line_start, and line_end are required" in reason
    ):
        response["next_action"] = (
            "Retry generate_issue only with an exact file_path and line range from "
            "a successful source tool output. Do not invent or shorten paths."
        )

    return response


async def _has_source_evidence(
    *,
    file_path: str,
    line_start: int,
    line_end: int,
) -> bool:
    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(runtime.job_id),
                "session_id": str(runtime.session_id),
                "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)},
            }
        )
        .to_list(length=None)
    )
    return line_range_is_covered(
        source_chunk_evidence(documents),
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


async def _evidence_rejection_reason(
    *,
    investigation_id: str,
    claim_type: AllowedClaimType,
    file_path: str | None,
    line_start: int | None,
    line_end: int | None,
    supporting_evidence: list[SourceEvidenceReference],
    contradicting_evidence: list[SourceEvidenceReference],
) -> str | None:
    delivered = await _delivered_source_evidence()
    missing_evidence = _missing_delivered_evidence(
        [*supporting_evidence, *contradicting_evidence],
        delivered,
    )
    if missing_evidence:
        missing_chunks = ", ".join(
            f"{reference.file_path}:chunk#{reference.chunk_index}"
            for reference in missing_evidence[:3]
        )
        if len(missing_evidence) > 3:
            missing_chunks = f"{missing_chunks}, ..."
        return (
            "AI issue rejected: evidence reference was not returned as full "
            f"source by read_file_chunk. Missing chunks: {missing_chunks}"
        )
    if file_path is not None and line_start is not None and line_end is not None:
        if not _evidence_references_cover_line_range(
            supporting_evidence,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        ):
            return _unsupported_source_range_reason(
                supporting_evidence=supporting_evidence,
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
            )
    if claim_type == "missing_behavior":
        modes = get_ai_tool_runtime().investigation_search_modes(investigation_id)
        if "exact" not in modes or not modes & {"auto", "semantic"}:
            return (
                "AI issue rejected: missing behavior requires semantic/auto discovery "
                "and an exact self-challenge search"
            )
    return None


def _missing_delivered_evidence(
    references: list[SourceEvidenceReference],
    delivered: list[SourceChunkEvidence],
) -> list[SourceEvidenceReference]:
    """Return evidence references that were not previously delivered as source."""

    return [
        reference
        for reference in references
        if not _matches_delivered_evidence(reference, delivered)
    ]


async def _delivered_source_evidence() -> list[SourceChunkEvidence]:
    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(runtime.job_id),
                "session_id": str(runtime.session_id),
                "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)},
            }
        )
        .to_list(length=None)
    )
    return source_chunk_evidence(documents)


def _matches_delivered_evidence(
    reference: SourceEvidenceReference,
    delivered: list[SourceChunkEvidence],
) -> bool:
    return any(
        item.file_path == reference.file_path
        and item.chunk_index == reference.chunk_index
        and item.line_start is not None
        and item.line_end is not None
        and item.line_start <= reference.line_start
        and item.line_end >= reference.line_end
        and (
            reference.tool_sequence is None
            or item.tool_sequence == reference.tool_sequence
        )
        for item in delivered
    )


def _evidence_references_cover_line_range(
    references: list[SourceEvidenceReference],
    *,
    file_path: str,
    line_start: int,
    line_end: int,
) -> bool:
    return line_range_is_covered(
        [
            SourceChunkEvidence(
                file_path=reference.file_path,
                chunk_index=reference.chunk_index,
                line_start=reference.line_start,
                line_end=reference.line_end,
                tool_sequence=reference.tool_sequence,
            )
            for reference in references
        ],
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


def _unsupported_source_range_reason(
    *,
    supporting_evidence: list[SourceEvidenceReference],
    file_path: str,
    line_start: int,
    line_end: int,
) -> str:
    ranges = _format_supporting_evidence_ranges(
        supporting_evidence,
        file_path=file_path,
    )
    if not ranges:
        ranges = "none for this file"
    return (
        "AI issue rejected: claimed source range is not supporting evidence; "
        f"claimed {file_path}:{line_start}-{line_end}; covered supporting "
        f"ranges: {ranges}. Use one contiguous covered range per issue; "
        "do not merge disjoint ranges into one issue range."
    )


def _format_supporting_evidence_ranges(
    references: list[SourceEvidenceReference],
    *,
    file_path: str,
) -> str:
    ranges = sorted(
        {
            (reference.line_start, reference.line_end)
            for reference in references
            if reference.file_path == file_path
        }
    )
    return ", ".join(f"{file_path}:{start}-{end}" for start, end in ranges)


def _behavior_evidence_owner_rejection_reason(
    *,
    category: AllowedIssueCategory,
    supporting_evidence: list[SourceEvidenceReference],
) -> str | None:
    if category not in {IssueCategory.REQUIREMENT.value, IssueCategory.SECURITY.value}:
        return None
    if any(
        _is_likely_runtime_evidence_path(reference.file_path)
        for reference in supporting_evidence
    ):
        return None

    return (
        "AI issue rejected: behavior/security issues need supporting evidence from the "
        "runtime implementation, not only tests, docs, specs, or verification "
        "harnesses"
    )


def _is_likely_runtime_evidence_path(file_path: str) -> bool:
    normalized_path = file_path.replace("\\", "/").lower()
    parts = {part for part in normalized_path.split("/") if part}
    filename = normalized_path.rsplit("/", 1)[-1]
    if parts & NON_RUNTIME_EVIDENCE_DIRS:
        return False
    if filename.startswith("test_") or filename.endswith(("_test.py", ".md", ".rst")):
        return False
    if filename.startswith("verify_") and not parts & RUNTIME_SOURCE_DIRS:
        return False

    return True


def _issue_evidence_fingerprint(
    *,
    investigation_id: str,
    claim_type: AllowedClaimType,
    supporting_evidence: list[SourceEvidenceReference],
    contradicting_evidence: list[SourceEvidenceReference],
) -> str:
    del investigation_id
    evidence = sorted(
        (
            reference.file_path,
            reference.chunk_index,
            reference.line_start,
            reference.line_end,
        )
        for reference in [*supporting_evidence, *contradicting_evidence]
    )
    payload = {
        "claim_type": claim_type,
        "evidence": evidence,
    }
    encoded = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _adjust_security_confidence(
    *,
    category: AllowedIssueCategory | None,
    confidence: float | None,
    references: list[str],
) -> float | None:
    if category != IssueCategory.SECURITY.value or confidence is None or references:
        return confidence

    return min(confidence, MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES)


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value

    return None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None

    return [str(item) for item in value]


async def _latest_rag_references() -> list[str]:
    runtime = get_ai_tool_runtime()
    document = await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION].find_one(
        {
            "job_id": str(runtime.job_id),
            "session_id": str(runtime.session_id),
            "tool_name": {
                "$in": [
                    "search_knowledge_base",
                    "search_coding_standard",
                    "roadmap_rule_catalog",
                ]
            },
            "output.status": "ok",
        },
        sort=[("sequence", -1)],
    )
    if document is None:
        return []

    if document.get("tool_name") == "roadmap_rule_catalog":
        return ["roadmap_rule_catalog"]

    output = document.get("output")
    if not isinstance(output, dict):
        return []

    references: list[str] = []
    for result in output.get("results", []):
        if not isinstance(result, dict):
            continue
        reference = _rag_result_reference(result)
        if reference is not None:
            references.append(reference)

    return references


async def _latest_kb_grounding(
    rule_id: str | None = None,
) -> tuple[list[str], dict[str, object]]:
    runtime = get_ai_tool_runtime()
    document = await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION].find_one(
        {
            "job_id": str(runtime.job_id),
            "session_id": str(runtime.session_id),
            "tool_name": "search_knowledge_base",
            "output.status": "ok",
        },
        sort=[("sequence", -1)],
    )
    if document is None:
        if rule_id is not None:
            return [], {}
        structure_document = await runtime.mongodb_database[
            TOOL_CALL_LOGS_COLLECTION
        ].find_one(
            {
                "job_id": str(runtime.job_id),
                "session_id": str(runtime.session_id),
                "tool_name": "analyze_project_structure",
                "output.roadmap": {"$exists": True},
            },
            sort=[("sequence", -1)],
        )
        if structure_document is None:
            return [], {}
        return ["knowledge_base checklist"], {"knowledge_doc_type": "roadmap_rule"}

    output = document.get("output")
    results = output.get("results") if isinstance(output, dict) else None
    if not isinstance(results, list):
        return [], {}

    references: list[str] = []
    grounding: dict[str, object] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        metadata = result.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if rule_id is not None and metadata.get("rule_id") != rule_id:
            continue
        reference = _rag_result_reference(result)
        if reference is not None:
            references.append(reference)
        if grounding:
            continue
        grounding["knowledge_doc_type"] = str(metadata.get("doc_type", "unknown"))
        for field in ("rule_id", "week", "priority"):
            value = metadata.get(field)
            if value is not None:
                grounding[field] = value

    return references, grounding


def _rag_result_reference(result: dict[str, Any]) -> str | None:
    source = result.get("source")
    if isinstance(source, str) and source:
        return source

    content = result.get("content")
    if isinstance(content, str) and content:
        return content[:160]

    return None


async def _find_existing_issue(
    *,
    file_path: str | None,
    line_start: int | None,
    category: IssueCategory,
) -> ReviewIssue | None:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewIssue)
        .where(
            ReviewIssue.job_id == runtime.job_id,
            ReviewIssue.file_path == file_path,
            ReviewIssue.line_start == line_start,
            ReviewIssue.category == category,
        )
        .order_by(ReviewIssue.created_at.asc())
    )
    return result.scalars().first()


async def _find_existing_issue_from_supporting_evidence(
    *,
    supporting_evidence: list[SourceEvidenceReference],
) -> ReviewIssue | None:
    runtime = get_ai_tool_runtime()
    for reference in supporting_evidence:
        result = await runtime.postgres_session.execute(
            select(ReviewIssue)
            .where(
                ReviewIssue.job_id == runtime.job_id,
                ReviewIssue.file_path == reference.file_path,
                ReviewIssue.line_start == reference.line_start,
                ReviewIssue.line_end == reference.line_end,
                ReviewIssue.source.in_([IssueSource.AI_REVIEW, IssueSource.KB]),
            )
            .order_by(ReviewIssue.created_at.asc())
        )
        existing_issue = result.scalars().first()
        if existing_issue is not None:
            return existing_issue

    return None


def validate_issue_payload(
    *,
    file_path: str | None,
    line_start: int | None,
    line_end: int | None,
    severity: str,
    category: str,
    title: str,
    description: str,
    suggestion: str | None,
    confidence: float,
    references: list[str],
    source: str = "ai_review",
    knowledge_metadata: dict[str, object] | None = None,
) -> None:
    """Validate an AI issue before any database write happens."""

    _ = severity
    if confidence < 0.7:
        raise IssueValidationError("AI issue rejected: confidence must be >= 0.7")

    if category == IssueCategory.SECURITY.value and not references:
        raise IssueValidationError(
            "AI issue rejected: security issues require a knowledge-base reference"
        )

    if file_path is None or line_start is None or line_end is None:
        raise IssueValidationError(
            "AI issue rejected: file_path, line_start, and line_end are required"
        )

    metadata = knowledge_metadata or {}
    if source == IssueSource.KB.value and metadata.get("knowledge_doc_type") == (
        "roadmap_rule"
    ):
        hidden_terms = ["roadmap"]
        rule_id = metadata.get("rule_id")
        if isinstance(rule_id, str):
            hidden_terms.append(rule_id.lower())
        visible_text = " ".join(
            value for value in (title, description, suggestion or "") if value
        ).lower()
        if any(term in visible_text for term in hidden_terms):
            raise IssueValidationError(
                "AI issue rejected: KB-derived issue text must describe the code "
                "problem without internal roadmap identifiers"
            )

    if line_start < 1 or line_end < line_start:
        raise IssueValidationError("AI issue rejected: invalid source line range")

    try:
        source_path = resolve_sandbox_file(file_path)
    except ValueError as error:
        raise IssueValidationError(f"AI issue rejected: {error}") from error

    line_count = len(
        source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    )
    if line_start > line_count or line_end > line_count:
        raise IssueValidationError(
            f"AI issue rejected: line range {line_start}-{line_end} "
            f"does not exist in {file_path} ({line_count} lines)"
        )


def _build_source_context(
    *,
    file_path: str,
    line_start: int,
    line_end: int,
    context_radius: int = 3,
) -> dict[str, object]:
    source_path = resolve_sandbox_file(file_path)
    lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    first_line = max(1, line_start - context_radius)
    last_line = min(len(lines), line_end + context_radius)
    return {
        "start_line": first_line,
        "lines": lines[first_line - 1 : last_line],
    }
