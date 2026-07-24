"""Probe evidence budget selection and full-audit bundle construction."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

from app.ai.probe.candidate_retrieval import _candidate_from_document
from app.ai.probe.contracts import ProbeDefinition, ProbeLane
from app.ai.probe.models import ProbeCandidateChunk, ProbeEvidenceBundle, _probe_id

FULL_AUDIT_CHUNKS_PER_PROBE = 6
TRACE_ID_HASH_LENGTH = 12


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
            f"{sha256(file_path.encode()).hexdigest()[:TRACE_ID_HASH_LENGTH]}.{index}"
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
