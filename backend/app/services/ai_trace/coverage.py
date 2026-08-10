"""Coverage metrics derived from persisted AI review artifacts."""

from __future__ import annotations

from typing import Any

from app.ai.review.source_evidence import source_chunk_keys


def _structure_coverage(document: dict[str, Any] | None) -> tuple[int, int]:
    if document is None:
        return 0, 0

    file_tree = document.get("file_tree", [])
    if not isinstance(file_tree, list):
        return 0, 0

    reviewable_entries = [
        entry
        for entry in file_tree
        if isinstance(entry, dict) and entry.get("should_review") is True
    ]
    total_lines = sum(
        _safe_int(entry.get("line_count")) for entry in reviewable_entries
    )
    return len(reviewable_entries), total_lines


def _read_chunk_coverage(
    documents: list[dict[str, Any]],
    *,
    target_chunk_keys: set[tuple[str, int]] | None = None,
) -> tuple[int, int, int]:
    read_chunks = source_chunk_keys(documents)
    read_files = {file_path for file_path, _chunk_index in read_chunks}

    if target_chunk_keys is None:
        read_target_chunks = len(read_chunks)
    else:
        read_target_chunks = len(read_chunks & target_chunk_keys)

    return len(read_files), len(read_chunks), read_target_chunks


def _probe_slot_counts(documents: list[dict[str, Any]]) -> tuple[int, int]:
    retrieved_count = 0
    judged_count = 0
    for document in documents:
        if document.get("tool_name") != "probe_retrieval":
            continue
        output = document.get("output")
        if not isinstance(output, dict):
            continue
        result_count = _safe_int(output.get("result_count"))
        retrieved_count += _safe_int(output.get("selected_count")) or result_count
        judged_count += _safe_int(output.get("sent_to_judge")) or result_count
    return retrieved_count, judged_count


def _broad_audited_chunk_count(documents: list[dict[str, Any]]) -> int:
    audited_keys: set[tuple[str, int]] = set()
    for document in documents:
        if document.get("tool_name") != "probe_retrieval":
            continue
        tool_input = document.get("input")
        if not isinstance(tool_input, dict) or tool_input.get("lane") != "coverage":
            continue
        output = document.get("output")
        results = output.get("results") if isinstance(output, dict) else None
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            file_path = result.get("file_path")
            chunk_index = result.get("chunk_index")
            if isinstance(file_path, str) and isinstance(chunk_index, int):
                audited_keys.add((file_path, chunk_index))
    return len(audited_keys)


def _percent(current: int, total: int) -> float:
    if total <= 0:
        return 0.0

    return round(min(100.0, (current / total) * 100.0), 1)


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) else 0
