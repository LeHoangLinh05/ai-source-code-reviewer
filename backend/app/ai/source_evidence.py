"""Helpers for source-chunk evidence recorded in redacted AI tool traces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

SOURCE_TOOL_NAMES = frozenset(
    {"read_file_chunk", "read_next_review_chunk", "search_code_semantic"}
)


@dataclass(slots=True, frozen=True)
class SourceChunkEvidence:
    """Location-only evidence for one full chunk delivered to the model."""

    file_path: str
    chunk_index: int
    line_start: int | None
    line_end: int | None


def source_chunk_evidence(
    documents: Sequence[object],
) -> list[SourceChunkEvidence]:
    """Extract successful chunk locations from read and semantic-search traces."""

    evidence: list[SourceChunkEvidence] = []
    for document in documents:
        if not isinstance(document, dict):
            continue

        output = document.get("output")
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue

        tool_name = document.get("tool_name")
        if tool_name == "search_code_semantic":
            evidence.extend(_semantic_evidence(output))
        elif tool_name in {"read_file_chunk", "read_next_review_chunk"} or (
            tool_name is None and "file_path" in output
        ):
            item = _evidence_item(output)
            if item is not None:
                evidence.append(item)

    return evidence


def source_chunk_keys(documents: Sequence[object]) -> set[tuple[str, int]]:
    """Return the union of chunk keys delivered by either source tool."""

    return {
        (item.file_path, item.chunk_index) for item in source_chunk_evidence(documents)
    }


def has_source_line_evidence(
    documents: Sequence[object],
    *,
    file_path: str,
    line_start: int,
    line_end: int,
) -> bool:
    """Return whether a delivered chunk contains the claimed source range."""

    return any(
        item.file_path == file_path
        and item.line_start is not None
        and item.line_end is not None
        and item.line_start <= line_start
        and item.line_end >= line_end
        for item in source_chunk_evidence(documents)
    )


def _semantic_evidence(output: dict[str, Any]) -> list[SourceChunkEvidence]:
    results = output.get("results")
    if not isinstance(results, list):
        return []

    evidence: list[SourceChunkEvidence] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("status", "ok") != "ok":
            continue
        item = _evidence_item(result)
        if item is not None:
            evidence.append(item)

    return evidence


def _evidence_item(payload: dict[str, Any]) -> SourceChunkEvidence | None:
    file_path = payload.get("file_path")
    chunk_index = payload.get("chunk_index")
    if not isinstance(file_path, str) or not isinstance(chunk_index, int):
        return None

    return SourceChunkEvidence(
        file_path=file_path,
        chunk_index=chunk_index,
        line_start=_optional_int(payload.get("line_start")),
        line_end=_optional_int(payload.get("line_end")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
