"""Probe candidate generation, structural matching, and rank fusion."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from app.ai.probe.contracts import ProbeDefinition
from app.ai.probe.models import ProbeCandidateChunk
from app.ai.rag.bm25_index import tokenize

MIN_IMPORTANT_QUERY_TERM_LENGTH = 3
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
