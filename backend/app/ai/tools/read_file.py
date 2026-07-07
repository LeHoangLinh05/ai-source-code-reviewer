"""AI tool for reading selected source file chunks."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.tools import tool

from app.ai.tool_runtime import get_ai_tool_runtime
from app.ai.tools.common import resolve_sandbox_file
from app.analyzers.code_chunker import chunk_python_file
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
)


@tool
async def read_file_chunk(
    job_id: str,
    file_path: str,
    chunk_index: int | None = None,
) -> dict[str, object]:
    """Đọc 1 chunk code cụ thể kèm metadata và các static issue đã phát hiện trong range đó. Dùng chunk_index khác để đọc parent context (class bao quanh, file header/imports) khi cần hiểu ngữ cảnh rộng hơn — KHÔNG tự động expand, agent phải tự gọi lại."""

    requested_chunk_index = chunk_index or 0
    metadata = await _load_persisted_chunk(
        job_id=UUID(job_id),
        file_path=file_path,
        chunk_index=requested_chunk_index,
    )
    if metadata is None:
        metadata = _build_chunk_from_source(file_path, requested_chunk_index)

    static_issues = await _static_issues_in_range(
        job_id=UUID(job_id),
        file_path=file_path,
        line_start=int(metadata["line_start"]),
        line_end=int(metadata["line_end"]),
    )
    content = metadata.get("chunk_text")
    if not isinstance(content, str):
        content = _read_source_range(
            file_path=file_path,
            line_start=int(metadata["line_start"]),
            line_end=int(metadata["line_end"]),
        )

    return {
        "content": content,
        "file_path": file_path,
        "chunk_index": int(metadata["chunk_index"]),
        "total_chunks": int(metadata["total_chunks"]),
        "line_start": int(metadata["line_start"]),
        "line_end": int(metadata["line_end"]),
        "language": str(metadata.get("language", "unknown")),
        "function_name": metadata.get("function_name"),
        "static_issues_in_range": static_issues,
    }


async def _load_persisted_chunk(
    *,
    job_id: UUID,
    file_path: str,
    chunk_index: int,
) -> dict[str, Any] | None:
    runtime = get_ai_tool_runtime()
    document = await runtime.mongodb_database[CHUNK_METADATA_COLLECTION].find_one(
        {
            "job_id": str(job_id),
            "file_path": file_path,
            "chunk_index": chunk_index,
        }
    )
    if document is None:
        return None

    return dict(document)


def _build_chunk_from_source(file_path: str, chunk_index: int) -> dict[str, Any]:
    source_path = resolve_sandbox_file(file_path)
    if source_path.suffix.lower() != ".py":
        lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not lines:
            raise ValueError(f"File is empty: {file_path}")
        return {
            "chunk_text": "\n".join(lines),
            "file_path": file_path,
            "language": "unknown",
            "chunk_index": 0,
            "total_chunks": 1,
            "function_name": None,
            "line_start": 1,
            "line_end": len(lines),
        }

    runtime = get_ai_tool_runtime()
    chunks = chunk_python_file(source_path, project_root=runtime.sandbox_path)
    if not chunks:
        raise ValueError(f"No readable chunks found for file: {file_path}")
    if chunk_index < 0 or chunk_index >= len(chunks):
        raise ValueError(
            f"chunk_index {chunk_index} out of range for {file_path}; "
            f"total_chunks={len(chunks)}"
        )

    chunk = chunks[chunk_index]
    metadata = chunk.metadata
    return {
        "chunk_text": chunk.content,
        "file_path": file_path,
        "language": metadata.language,
        "chunk_index": metadata.chunk_index,
        "total_chunks": metadata.total_chunks,
        "function_name": metadata.function_name,
        "line_start": metadata.line_start,
        "line_end": metadata.line_end,
    }


async def _static_issues_in_range(
    *,
    job_id: UUID,
    file_path: str,
    line_start: int,
    line_end: int,
) -> list[dict[str, object]]:
    runtime = get_ai_tool_runtime()
    static_documents = (
        await runtime.mongodb_database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION]
        .find({"job_id": str(job_id)})
        .to_list(length=None)
    )
    issues: list[dict[str, object]] = []
    for document in static_documents:
        tool_name = str(document.get("tool", "static"))
        for issue in document.get("parsed_issues", []):
            if not isinstance(issue, dict):
                continue
            if issue.get("file_path") != file_path:
                continue
            issue_line_start = issue.get("line_start")
            if not isinstance(issue_line_start, int):
                continue
            issue_line_end = int(issue.get("line_end") or issue_line_start)
            if issue_line_start > line_end or issue_line_end < line_start:
                continue
            issues.append(
                {
                    "source": tool_name,
                    "severity": str(issue.get("severity", "")),
                    "title": str(issue.get("rule_id") or issue.get("message", "")),
                    "line": issue_line_start,
                    "message": str(issue.get("message", "")),
                }
            )

    return issues


def _read_source_range(*, file_path: str, line_start: int, line_end: int) -> str:
    source_path = resolve_sandbox_file(file_path)
    lines = source_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[line_start - 1 : line_end])
