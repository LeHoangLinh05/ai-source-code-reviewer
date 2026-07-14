"""AI tool for reading selected source file chunks."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from app.ai.source_evidence import SOURCE_TOOL_NAMES, source_chunk_keys
from app.ai.tool_runtime import ensure_ai_job_active, get_ai_tool_runtime
from app.ai.tools.common import (
    parse_json_object_text,
    parse_job_uuid_or_current,
    resolve_sandbox_file,
    unwrap_react_json_input,
)
from app.analyzers.code_chunker import chunk_python_file
from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
    TOOL_CALL_LOGS_COLLECTION,
)


class ReadFileChunkInput(BaseModel):
    """Input schema for reading one source chunk."""

    job_id: str | None = None
    file_path: str
    chunk_index: int | None = None
    required_chunk_indexes: list[int] | None = None

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
    required_chunk_indexes: list[int] | None = None,
) -> dict[str, object]:
    """Đọc 1 chunk code cụ thể kèm metadata và các static issue đã phát hiện trong range đó. chunk_index là zero-based: nếu total_chunks=2 thì index hợp lệ là 0 và 1. Dùng chunk_index khác để đọc parent context (class bao quanh, file header/imports) khi cần hiểu ngữ cảnh rộng hơn — KHÔNG tự động expand, agent phải tự gọi lại."""

    return await _read_file_chunk_impl(
        file_path=file_path,
        job_id=job_id,
        chunk_index=chunk_index,
        required_chunk_indexes=required_chunk_indexes,
    )


@tool
async def read_next_review_chunk(job_id: str | None = None) -> dict[str, object]:
    """Đọc target chunk kế tiếp trong chunk_review_plan. Không nhận file_path/chunk_index từ AI để tránh bịa path hoặc đọc sai thứ tự."""

    await ensure_ai_job_active()
    job_uuid = parse_job_uuid_or_current(job_id)

    from app.ai.tools.generate_report import _load_chunk_review_coverage

    reviewed_chunks, total_chunks, missing_chunks = await _load_chunk_review_coverage(
        job_uuid,
        missing_limit=1,
    )
    if not missing_chunks:
        return {
            "status": "complete",
            "reviewed_chunks": reviewed_chunks,
            "total_chunks": total_chunks,
        }

    next_chunk = missing_chunks[0]
    file_path = str(next_chunk["file_path"])
    raw_chunk_index = next_chunk["chunk_index"]
    if not isinstance(raw_chunk_index, int):
        raise RuntimeError("Review plan returned an invalid chunk_index")
    chunk_index = raw_chunk_index
    response = await _read_file_chunk_impl(
        file_path=file_path,
        job_id=str(job_uuid),
        chunk_index=chunk_index,
        required_chunk_indexes=None,
    )
    response["reviewed_chunks_before_read"] = reviewed_chunks
    response["total_target_chunks"] = total_chunks
    return response


async def _read_file_chunk_impl(
    *,
    file_path: str,
    job_id: str | None,
    chunk_index: int | None,
    required_chunk_indexes: list[int] | None,
) -> dict[str, object]:
    file_path, job_id, chunk_index, required_chunk_indexes = normalize_read_file_input(
        file_path=file_path,
        job_id=job_id,
        chunk_index=chunk_index,
        required_chunk_indexes=required_chunk_indexes,
    )
    await ensure_ai_job_active()
    job_uuid = parse_job_uuid_or_current(job_id)
    requested_chunk_index = await _resolve_requested_chunk_index(
        job_id=job_uuid,
        file_path=file_path,
        chunk_index=chunk_index,
        required_chunk_indexes=required_chunk_indexes,
    )
    if requested_chunk_index is None:
        return _build_skipped_read_response(
            file_path=file_path,
            reason="all required_chunk_indexes for this file were already read",
            required_chunk_indexes=required_chunk_indexes or [],
        )

    requested_file_path = file_path
    metadata = await _load_persisted_chunk(
        job_id=job_uuid,
        file_path=file_path,
        chunk_index=requested_chunk_index,
    )
    if metadata is None:
        resolved_file_path = await _resolve_known_file_path_alias(
            job_id=job_uuid,
            file_path=file_path,
            chunk_index=requested_chunk_index,
        )
        if resolved_file_path is not None:
            file_path = resolved_file_path
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
                file_path_suggestions=await _file_path_suggestions(
                    job_id=job_uuid,
                    file_path=file_path,
                ),
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

    runtime = get_ai_tool_runtime()
    is_new_content, content_sha256, content_size = runtime.register_chunk_content(
        file_path=file_path,
        chunk_index=int(metadata["chunk_index"]),
        content=content,
    )
    if not is_new_content:
        return {
            "status": "deduplicated",
            "reason": "chunk content was already provided in this review session",
            "file_path": file_path,
            "chunk_index": int(metadata["chunk_index"]),
            "line_start": int(metadata["line_start"]),
            "line_end": int(metadata["line_end"]),
            "content_sha256": content_sha256,
            "content_size": content_size,
            **_path_resolution_metadata(
                requested_file_path=requested_file_path,
                resolved_file_path=file_path,
            ),
        }

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
        **_path_resolution_metadata(
            requested_file_path=requested_file_path,
            resolved_file_path=file_path,
        ),
    }


def _build_rejected_read_response(
    *,
    file_path: str,
    reason: str,
    requested_chunk_index: int,
    file_path_suggestions: list[str] | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "status": "rejected",
        "reason": reason,
        "file_path": file_path,
        "requested_chunk_index": requested_chunk_index,
    }
    if file_path_suggestions:
        response["file_path_suggestions"] = file_path_suggestions
        response["next_action"] = (
            "Retry read_file_chunk with an exact path from file_path_suggestions "
            "or chunk_review_plan instead of inventing file paths."
        )

    return response


def _build_skipped_read_response(
    *,
    file_path: str,
    reason: str,
    required_chunk_indexes: list[int],
) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": reason,
        "file_path": file_path,
        "required_chunk_indexes": required_chunk_indexes,
    }


async def _resolve_requested_chunk_index(
    *,
    job_id: UUID,
    file_path: str,
    chunk_index: int | None,
    required_chunk_indexes: list[int] | None,
) -> int | None:
    if chunk_index is not None:
        return chunk_index
    if not required_chunk_indexes:
        return 0

    reviewed_indexes = await _reviewed_chunk_indexes_for_file(
        job_id=job_id,
        file_path=file_path,
    )
    return _first_unread_required_chunk_index(
        required_chunk_indexes=required_chunk_indexes,
        reviewed_indexes=reviewed_indexes,
    )


def _first_unread_required_chunk_index(
    *,
    required_chunk_indexes: list[int],
    reviewed_indexes: set[int],
) -> int | None:
    for required_chunk_index in required_chunk_indexes:
        if required_chunk_index not in reviewed_indexes:
            return required_chunk_index

    return None


async def _reviewed_chunk_indexes_for_file(
    *,
    job_id: UUID,
    file_path: str,
) -> set[int]:
    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(job_id),
                "tool_name": {"$in": sorted(SOURCE_TOOL_NAMES)},
            }
        )
        .to_list(length=None)
    )
    return {
        chunk_index
        for reviewed_file_path, chunk_index in source_chunk_keys(documents)
        if reviewed_file_path == file_path
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


async def _file_path_suggestions(
    *,
    job_id: UUID,
    file_path: str,
) -> list[str]:
    runtime = get_ai_tool_runtime()
    documents = (
        await runtime.mongodb_database[CHUNK_METADATA_COLLECTION]
        .find({"job_id": str(job_id)}, {"file_path": 1, "_id": 0})
        .to_list(length=None)
    )
    known_file_paths = sorted(
        {
            str(document.get("file_path"))
            for document in documents
            if isinstance(document, dict) and isinstance(document.get("file_path"), str)
        }
    )
    return _closest_file_path_suggestions(
        requested_file_path=file_path,
        known_file_paths=known_file_paths,
    )


async def _resolve_known_file_path_alias(
    *,
    job_id: UUID,
    file_path: str,
    chunk_index: int,
) -> str | None:
    candidate_paths: list[str] = []
    for candidate_path in await _file_path_suggestions(
        job_id=job_id,
        file_path=file_path,
    ):
        if candidate_path == file_path:
            continue
        if (
            await _load_persisted_chunk(
                job_id=job_id,
                file_path=candidate_path,
                chunk_index=chunk_index,
            )
            is not None
        ):
            candidate_paths.append(candidate_path)

    return candidate_paths[0] if len(candidate_paths) == 1 else None


def _closest_file_path_suggestions(
    *,
    requested_file_path: str,
    known_file_paths: list[str],
) -> list[str]:
    if not known_file_paths:
        return []

    normalized_requested = requested_file_path.replace("\\", "/").lower()
    normalized_by_path = {
        known_file_path.replace("\\", "/").lower(): known_file_path
        for known_file_path in known_file_paths
    }
    requested_basename = normalized_requested.rsplit("/", 1)[-1]
    basename_matches = [
        known_file_path
        for normalized_path, known_file_path in normalized_by_path.items()
        if normalized_path.rsplit("/", 1)[-1] == requested_basename
    ]
    matches = get_close_matches(
        normalized_requested,
        list(normalized_by_path),
        n=5,
        cutoff=0.45,
    )
    suggestions = [*basename_matches, *(normalized_by_path[match] for match in matches)]
    return list(dict.fromkeys(suggestions))[:5]


def _path_resolution_metadata(
    *,
    requested_file_path: str,
    resolved_file_path: str,
) -> dict[str, str]:
    if requested_file_path == resolved_file_path:
        return {}
    return {
        "requested_file_path": requested_file_path,
        "path_resolution": "unique_file_path_suggestion",
    }


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
    required_chunk_indexes: list[int] | None = None,
) -> tuple[str, str | None, int | None, list[int] | None]:
    """Normalize raw ReAct JSON strings before filesystem access."""

    parsed_input = parse_json_object_text(file_path)
    if parsed_input is None:
        return file_path, job_id, chunk_index, required_chunk_indexes

    normalized_file_path = str(parsed_input.get("file_path", file_path))
    normalized_job_id = _optional_str(parsed_input.get("job_id")) or job_id
    parsed_chunk_index = _optional_int(parsed_input.get("chunk_index"))
    parsed_required_chunk_indexes = _optional_int_list(
        parsed_input.get("required_chunk_indexes")
    )
    if parsed_chunk_index is None:
        return (
            normalized_file_path,
            normalized_job_id,
            chunk_index,
            parsed_required_chunk_indexes or required_chunk_indexes,
        )

    return (
        normalized_file_path,
        normalized_job_id,
        parsed_chunk_index,
        parsed_required_chunk_indexes or required_chunk_indexes,
    )


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


def _optional_int_list(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None

    indexes: list[int] = []
    for item in value:
        parsed_item = _optional_int(item)
        if parsed_item is not None:
            indexes.append(parsed_item)

    return indexes or None


def _optional_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    return [str(item) for item in value]
