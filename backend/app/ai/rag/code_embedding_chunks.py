"""Cache identity, metadata, filtering, and batching for code embeddings."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

from app.schemas.mongodb import ChunkMetadataDocument

CODE_EMBEDDING_MODEL_VERSION = "remote-api-v1"
UNKNOWN_CODE_EMBEDDING_DIMENSION = 0
CODE_CHUNKER_VERSION = "v1"


def prepare_code_embedding_chunks(
    chunks: list[ChunkMetadataDocument],
    *,
    repository_id: str | UUID | None,
    branch: str | None,
    commit_sha: str | None,
    provider: str,
    model_name: str,
    model_version: str,
    dimension: int | None,
) -> list[ChunkMetadataDocument]:
    """Attach deterministic repo-branch cache metadata to source chunks."""

    if repository_id is None:
        return chunks

    normalized_branch = _normalize_branch(branch)
    if normalized_branch is None:
        return chunks

    repo_branch_key = build_repo_branch_key(
        repository_id=repository_id,
        branch=normalized_branch,
    )
    index_generation_key = build_index_generation_key(
        repo_branch_key=repo_branch_key,
        commit_sha=commit_sha,
    )
    occurrences: dict[tuple[str, str], int] = {}
    prepared_chunks: list[ChunkMetadataDocument] = []
    for chunk in chunks:
        content = chunk.chunk_text if isinstance(chunk.chunk_text, str) else ""
        content_hash = build_content_hash(content)
        occurrence_key = (chunk.file_path, content_hash)
        occurrence_index = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence_index + 1
        embedding_cache_id = build_embedding_cache_id(
            index_generation_key=index_generation_key,
            file_path=chunk.file_path,
            content_hash=content_hash,
            occurrence_index=occurrence_index,
            provider=provider,
            model_name=model_name,
            model_version=model_version,
            dimension=dimension,
            chunker_version=CODE_CHUNKER_VERSION,
        )
        prepared_chunks.append(
            chunk.model_copy(
                update={
                    "repository_id": repository_id,
                    "branch": normalized_branch,
                    "commit_sha": commit_sha,
                    "repo_branch_key": repo_branch_key,
                    "index_generation_key": index_generation_key,
                    "content_hash": content_hash,
                    "embedding_cache_id": embedding_cache_id,
                    "occurrence_index": occurrence_index,
                    "chunker_version": CODE_CHUNKER_VERSION,
                }
            )
        )

    return prepared_chunks


def build_repo_branch_key(*, repository_id: str | UUID, branch: str) -> str:
    """Return a stable cache scope for one repository branch."""

    return _sha256_text(f"{repository_id}\0{branch}")


def build_index_generation_key(*, repo_branch_key: str, commit_sha: str | None) -> str:
    """Return the immutable vector scope for one branch snapshot."""

    normalized_commit = (commit_sha or "").strip()
    if not normalized_commit:
        return repo_branch_key

    return _sha256_text(f"{repo_branch_key}\0{normalized_commit}")


def build_content_hash(content: str) -> str:
    """Return the content hash used to detect changed chunks."""

    return _sha256_text(content)


def build_embedding_cache_id(
    *,
    index_generation_key: str,
    file_path: str,
    content_hash: str,
    occurrence_index: int,
    provider: str,
    model_name: str,
    model_version: str,
    dimension: int | None,
    chunker_version: str,
) -> str:
    """Return the vector id for one cacheable source chunk."""

    fingerprint = "\0".join(
        [
            index_generation_key,
            file_path,
            content_hash,
            str(occurrence_index),
            provider,
            model_name,
            model_version,
            str(dimension or UNKNOWN_CODE_EMBEDDING_DIMENSION),
            chunker_version,
        ]
    )
    return _sha256_text(fingerprint)


def code_embedding_model_version(provider: str) -> str:
    """Return the cache-significant model version label for a provider."""

    _ = provider
    return CODE_EMBEDDING_MODEL_VERSION


def _chunk_id(chunk: ChunkMetadataDocument) -> str:
    if chunk.embedding_cache_id:
        return chunk.embedding_cache_id

    return f"{chunk.job_id}:{chunk.file_path}:{chunk.chunk_index}"


def _chunk_metadata(
    chunk: ChunkMetadataDocument,
) -> dict[str, str | int | float | bool]:
    metadata: dict[str, str | int | float | bool] = {
        "job_id": str(chunk.job_id),
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "function_name": chunk.function_name or "",
        "class_name": chunk.class_name or "",
        "language": chunk.language,
        "risk_area": chunk.risk_area or "general",
        "module": chunk.module or "",
        "imports": json.dumps(chunk.imports, ensure_ascii=False),
    }
    optional_values: dict[str, str | int | None] = {
        "repository_id": str(chunk.repository_id) if chunk.repository_id else None,
        "branch": chunk.branch,
        "commit_sha": chunk.commit_sha,
        "repo_branch_key": chunk.repo_branch_key,
        "index_generation_key": chunk.index_generation_key,
        "content_hash": chunk.content_hash,
        "embedding_cache_id": chunk.embedding_cache_id,
        "occurrence_index": chunk.occurrence_index,
        "chunker_version": chunk.chunker_version,
    }
    for key, value in optional_values.items():
        if value is not None:
            metadata[key] = value

    return metadata


def _common_repo_branch_key(chunks: list[ChunkMetadataDocument]) -> str | None:
    repo_branch_keys = {
        chunk.repo_branch_key for chunk in chunks if chunk.repo_branch_key
    }
    if len(repo_branch_keys) == 1:
        return next(iter(repo_branch_keys))

    return None


def _common_index_generation_key(chunks: list[ChunkMetadataDocument]) -> str | None:
    index_generation_keys = {
        chunk.index_generation_key for chunk in chunks if chunk.index_generation_key
    }
    if len(index_generation_keys) == 1:
        return next(iter(index_generation_keys))

    return None


def _count_sensitive_chunks(chunks: list[ChunkMetadataDocument]) -> int:
    return sum(1 for chunk in chunks if not _can_send_chunk_to_remote(chunk))


def _can_send_chunk_to_remote(chunk: ChunkMetadataDocument) -> bool:
    content = chunk.chunk_text if isinstance(chunk.chunk_text, str) else ""
    return not _contains_high_confidence_secret(content)


def _contains_high_confidence_secret(content: str) -> bool:
    if "PRIVATE KEY-----" in content:
        return True

    secret_patterns = (
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bghp_[A-Za-z0-9_]{30,}\b",
        r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
        r"\bsk-[A-Za-z0-9]{32,}\b",
        r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][^'\"]{24,}",
    )
    return any(re.search(pattern, content) for pattern in secret_patterns)


def _normalize_branch(branch: str | None) -> str | None:
    if branch is None:
        return None

    normalized = branch.strip()
    return normalized or None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token_limited_batches(
    chunks: list[ChunkMetadataDocument],
    *,
    max_items: int,
    max_tokens: int,
    max_item_tokens: int,
) -> list[list[ChunkMetadataDocument]]:
    batches: list[list[ChunkMetadataDocument]] = []
    current_batch: list[ChunkMetadataDocument] = []
    current_tokens = 0
    for chunk in chunks:
        chunk_tokens = min(max(chunk.token_count, 1), max_item_tokens)
        if current_batch and (
            len(current_batch) >= max_items
            or current_tokens + chunk_tokens > max_tokens
        ):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(chunk)
        current_tokens += chunk_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _truncate_embedding_text(text: str, *, max_tokens: int) -> str:
    max_chars = max(1, max_tokens * 6)
    if len(text) > max_chars:
        text = text[:max_chars]

    tokens = re.findall(r"\S+", text)
    if len(tokens) <= max_tokens:
        return text

    return " ".join(tokens[:max_tokens])
