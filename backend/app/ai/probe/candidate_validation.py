"""Validate probe judge candidates against retrieved source evidence."""

import json
import logging
import re

from app.ai.probe.judging import _issue_category, _issue_severity
from app.ai.probe.models import (
    ProbeCandidateChunk,
    ProbeEvidenceBundle,
    ProbeJudgeIssueCandidate,
    _probe_id,
)
from app.ai.rag.bm25_index import tokenize
from app.ai.roadmap.knowledge import RoadmapRequirement
from app.models.review_issue import IssueCategory

logger = logging.getLogger(__name__)

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


def _supporting_bundle_chunk(
    candidate: ProbeJudgeIssueCandidate,
    bundles: list[ProbeEvidenceBundle],
) -> ProbeCandidateChunk | None:
    chunks = _supporting_bundle_chunks(candidate, bundles)
    return chunks[0] if chunks else None


def _supporting_bundle_chunks(
    candidate: ProbeJudgeIssueCandidate,
    bundles: list[ProbeEvidenceBundle],
) -> list[ProbeCandidateChunk]:
    file_path = candidate.file_path
    line_start = candidate.line_start
    line_end = candidate.line_end
    if (
        file_path is None
        or line_start is None
        or line_end is None
        or not candidate.supporting_evidence
    ):
        return []

    candidate_range = range(line_start, line_end + 1)
    chunks_by_key = {
        chunk.key: chunk for bundle in bundles for chunk in bundle.candidate_chunks
    }
    matched_chunks: dict[tuple[str, int], ProbeCandidateChunk] = {}
    anchor_chunks: dict[tuple[str, int], ProbeCandidateChunk] = {}

    for evidence in candidate.supporting_evidence:
        evidence_chunks = _resolve_evidence_chunks(
            evidence_file_path=evidence.file_path,
            evidence_chunk_index=evidence.chunk_index,
            evidence_line_start=evidence.line_start,
            evidence_line_end=evidence.line_end,
            chunks_by_key=chunks_by_key,
        )
        if not evidence_chunks:
            return []
        evidence_range = range(evidence.line_start, evidence.line_end + 1)
        for chunk in evidence_chunks:
            matched_chunks[chunk.key] = chunk
            if chunk.file_path == file_path and _ranges_overlap(
                candidate_range,
                evidence_range,
            ):
                anchor_chunks[chunk.key] = chunk

    if not anchor_chunks:
        return []

    candidate_file_chunks = [
        chunk
        for chunk in chunks_by_key.values()
        if chunk.file_path == file_path
        and chunk.line_start <= line_end
        and line_start <= chunk.line_end
    ]
    start_is_supported = any(
        chunk.line_start <= line_start <= chunk.line_end
        for chunk in candidate_file_chunks
    )
    end_is_supported = any(
        chunk.line_start <= line_end <= chunk.line_end
        for chunk in candidate_file_chunks
    )
    if not start_is_supported or not end_is_supported:
        return []

    return sorted(
        matched_chunks.values(),
        key=lambda chunk: (
            chunk.file_path != file_path,
            chunk.line_start,
            chunk.line_end,
            chunk.chunk_index,
        ),
    )


def _resolve_evidence_chunks(
    *,
    evidence_file_path: str,
    evidence_chunk_index: int,
    evidence_line_start: int,
    evidence_line_end: int,
    chunks_by_key: dict[tuple[str, int], ProbeCandidateChunk],
) -> list[ProbeCandidateChunk]:
    referenced_chunk = chunks_by_key.get((evidence_file_path, evidence_chunk_index))
    if referenced_chunk is None or not (
        referenced_chunk.line_start <= evidence_line_end
        and evidence_line_start <= referenced_chunk.line_end
    ):
        return []

    overlapping_chunks = [
        chunk
        for chunk in chunks_by_key.values()
        if chunk.file_path == evidence_file_path
        and chunk.line_start <= evidence_line_end
        and evidence_line_start <= chunk.line_end
    ]
    start_is_supported = any(
        chunk.line_start <= evidence_line_start <= chunk.line_end
        for chunk in overlapping_chunks
    )
    end_is_supported = any(
        chunk.line_start <= evidence_line_end <= chunk.line_end
        for chunk in overlapping_chunks
    )
    return overlapping_chunks if start_is_supported and end_is_supported else []


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
