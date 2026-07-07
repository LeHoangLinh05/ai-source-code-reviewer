"""AI tool for reading selected source file chunks."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    parse_job_uuid,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
from app.analyzers.code_chunker import chunk_python_file
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
)


class ReadFileChunkInput(BaseModel):
    """Input schema for reading one source chunk."""

    job_id: str | None = None
    file_path: str
    chunk_index: int | None = None

    @model_validator(mode="before")
    @classmethod
    def unwrap_react_json(cls, data: object) -> object:
        data = unwrap_react_json_input(data, "job_id")
        return unwrap_react_json_input(data, "file_path")


@tool(args_schema=ReadFileChunkInput)
async def read_file_chunk(
    file_path: str,
    job_id: str | None = None,
    chunk_index: int | None = None,
) -> dict[str, object]:
    """Đọc 1 chunk code cụ thể kèm metadata và các static issue đã phát hiện trong range đó. chunk_index là zero-based: nếu total_chunks=2 thì index hợp lệ là 0 và 1. Dùng chunk_index khác để đọc parent context (class bao quanh, file header/imports) khi cần hiểu ngữ cảnh rộng hơn — KHÔNG tự động expand, agent phải tự gọi lại."""

    file_path, job_id, chunk_index = normalize_read_file_input(
        file_path=file_path,
        job_id=job_id,
        chunk_index=chunk_index,
    )
    requested_chunk_index = chunk_index or 0
    runtime = get_ai_tool_runtime()
    await ensure_ai_job_active()
    job_uuid = parse_job_uuid(job_id or runtime.job_id)
    metadata = await _load_persisted_chunk(
        job_id=job_uuid,
        file_path=file_path,
        chunk_index=requested_chunk_index,
    )
    if metadata is None:
        try:
            metadata = _build_chunk_from_source(file_path, requested_chunk_index)
        except ValueError as error:
            return _build_rejected_read_response(
                file_path=file_path,
                reason=str(error),
                requested_chunk_index=requested_chunk_index,
            )

    static_issues = await _static_issues_in_range(
        job_id=job_uuid,
        file_path=file_path,
        line_start=int(metadata["line_start"]),
        line_end=int(metadata["line_end"]),
    )
    context_hints = await _context_hints(
        job_id=job_uuid,
        file_path=file_path,
        metadata=metadata,
    )
    content = metadata.get("chunk_text")
    if not isinstance(content, str):
        content = _read_source_range(
            file_path=file_path,
            line_start=int(metadata["line_start"]),
            line_end=int(metadata["line_end"]),
        )

    return {
        "status": "ok",
        "content": content,
        "file_path": file_path,
        "chunk_index": int(metadata["chunk_index"]),
        "total_chunks": int(metadata["total_chunks"]),
        "line_start": int(metadata["line_start"]),
        "line_end": int(metadata["line_end"]),
        "language": str(metadata.get("language", "unknown")),
        "chunk_type": str(metadata.get("chunk_type", "unknown")),
        "function_name": metadata.get("function_name"),
        "class_name": metadata.get("class_name"),
        "module": str(metadata.get("module", "")),
        "risk_area": str(metadata.get("risk_area", "general")),
        "imports": _optional_str_list(metadata.get("imports")),
        "token_count": int(metadata.get("token_count") or 0),
        "context_hints": context_hints,
        "static_issues_in_range": static_issues,
    }


def _build_rejected_read_response(
    *,
    file_path: str,
    reason: str,
    requested_chunk_index: int,
) -> dict[str, object]:
    return {
        "status": "rejected",
        "reason": reason,
        "file_path": file_path,
        "requested_chunk_index": requested_chunk_index,
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
            "chunk_type": "file",
            "chunk_index": 0,
            "total_chunks": 1,
            "function_name": None,
            "class_name": None,
            "line_start": 1,
            "line_end": len(lines),
            "module": "root",
            "risk_area": "general",
            "imports": [],
            "token_count": len(lines),
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
        "chunk_type": metadata.chunk_type,
        "chunk_index": metadata.chunk_index,
        "total_chunks": metadata.total_chunks,
        "function_name": metadata.function_name,
        "class_name": metadata.class_name,
        "line_start": metadata.line_start,
        "line_end": metadata.line_end,
        "module": metadata.module,
        "risk_area": metadata.risk_area,
        "imports": metadata.imports,
        "token_count": metadata.token_count,
    }


async def _context_hints(
    *,
    job_id: UUID,
    file_path: str,
    metadata: dict[str, Any],
) -> dict[str, object]:
    current_index = int(metadata.get("chunk_index") or 0)
    total_chunks = int(metadata.get("total_chunks") or 1)
    neighbor_indexes = [
        index
        for index in (current_index - 1, current_index + 1)
        if 0 <= index < total_chunks
    ]
    parent_indexes = await _parent_chunk_indexes(
        job_id=job_id,
        file_path=file_path,
        class_name=metadata.get("class_name"),
        current_index=current_index,
    )
    return {
        "parent_chunk_indexes": parent_indexes,
        "neighbor_chunk_indexes": neighbor_indexes,
    }


async def _parent_chunk_indexes(
    *,
    job_id: UUID,
    file_path: str,
    class_name: object,
    current_index: int,
) -> list[int]:
    if not isinstance(class_name, str) or not class_name:
        return []

    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[CHUNK_METADATA_COLLECTION]
        .find(
            {
                "job_id": str(job_id),
                "file_path": file_path,
                "class_name": class_name,
                "chunk_type": "class",
            }
        )
        .to_list(length=None)
    )
    parent_indexes: list[int] = []
    for document in documents:
        chunk_index = document.get("chunk_index")
        if isinstance(chunk_index, int) and chunk_index != current_index:
            parent_indexes.append(chunk_index)

    return sorted(parent_indexes)


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


def normalize_read_file_input(
    *,
    file_path: str,
    job_id: str | None,
    chunk_index: int | None,
) -> tuple[str, str | None, int | None]:
    """Normalize raw ReAct JSON strings before filesystem access."""

    parsed_input = parse_json_object_text(file_path)
    if parsed_input is None:
        return file_path, job_id, chunk_index

    normalized_file_path = str(parsed_input.get("file_path", file_path))
    normalized_job_id = _optional_str(parsed_input.get("job_id")) or job_id
    parsed_chunk_index = _optional_int(parsed_input.get("chunk_index"))
    if parsed_chunk_index is None:
        return normalized_file_path, normalized_job_id, chunk_index

    return normalized_file_path, normalized_job_id, parsed_chunk_index


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


def _optional_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    return [str(item) for item in value]
