"""Build persisted source-chunk documents for semantic indexing."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from app.analyzers.code_chunker import (
    CodeChunk,
    chunk_plain_text_file,
    chunk_python_file,
)
from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.structure_analyzer import LANGUAGE_BY_EXTENSION
from app.schemas.mongodb import ChunkMetadataDocument
from app.schemas.normalized_issue import NormalizedIssue

logger = logging.getLogger(__name__)


def build_source_chunk_documents(
    *,
    job_id: UUID,
    sandbox_path: Path,
    filtered_files: list[Path],
    issues: list[NormalizedIssue],
) -> list[ChunkMetadataDocument]:
    """Build code chunk documents for all supported source files."""

    python_files = [
        file_path for file_path in filtered_files if file_path.suffix == ".py"
    ]
    python_file_set = set(python_files)
    logger.info(
        "Review job %s chunking started: %d filtered files, %d Python files",
        job_id,
        len(filtered_files),
        len(python_files),
    )
    chunk_documents = _build_python_chunk_documents(
        job_id=job_id,
        sandbox_path=sandbox_path,
        python_files=python_files,
        issues=issues,
    )
    for file_path in filtered_files:
        if file_path in python_file_set:
            continue
        chunk_documents.extend(
            build_plain_file_chunk_metadata_documents(
                job_id=job_id,
                sandbox_path=sandbox_path,
                file_path=file_path,
                issues=issues,
            )
        )
    return chunk_documents


def _build_python_chunk_documents(
    *,
    job_id: UUID,
    sandbox_path: Path,
    python_files: list[Path],
    issues: list[NormalizedIssue],
) -> list[ChunkMetadataDocument]:
    documents: list[ChunkMetadataDocument] = []
    for file_path in python_files:
        file_chunks = chunk_python_file(
            file_path,
            project_root=sandbox_path,
            static_issues=issues,
        )
        logger.debug(
            "Review job %s chunked Python file %s into %d chunks",
            job_id,
            to_relative_posix_path(file_path, sandbox_path),
            len(file_chunks),
        )
        documents.extend(
            _chunk_metadata_document(job_id=job_id, chunk=chunk)
            for chunk in file_chunks
        )
    return documents


def build_plain_file_chunk_metadata(
    *,
    job_id: UUID,
    sandbox_path: Path,
    file_path: Path,
    issues: list[NormalizedIssue],
) -> ChunkMetadataDocument:
    """Return the first generated plain-file chunk."""

    chunks = build_plain_file_chunk_metadata_documents(
        job_id=job_id,
        sandbox_path=sandbox_path,
        file_path=file_path,
        issues=issues,
    )
    if not chunks:
        raise ValueError(f"Plain file is not reviewable: {file_path}")
    return chunks[0]


def build_plain_file_chunk_metadata_documents(
    *,
    job_id: UUID,
    sandbox_path: Path,
    file_path: Path,
    issues: list[NormalizedIssue],
) -> list[ChunkMetadataDocument]:
    """Represent a non-Python text source file as reviewable chunks."""

    if not should_chunk_plain_file(file_path):
        return []

    language = LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower()) or "text"
    chunks = chunk_plain_text_file(
        file_path,
        language=language,
        project_root=sandbox_path,
        static_issues=issues,
    )
    return [
        _chunk_metadata_document(job_id=job_id, chunk=chunk)
        for chunk in chunks
        if chunk.content.strip()
    ]


def should_chunk_plain_file(file_path: Path) -> bool:
    """Return whether a non-Python file should enter semantic code chunks."""

    if file_path.suffix.lower() != ".md":
        return True
    return file_path.name.lower() in {"readme.md", "readme.markdown"}


def _chunk_metadata_document(
    *,
    job_id: UUID,
    chunk: CodeChunk,
) -> ChunkMetadataDocument:
    metadata = chunk.metadata
    return ChunkMetadataDocument(
        job_id=job_id,
        file_path=metadata.file_path,
        language=metadata.language,
        chunk_type=metadata.chunk_type,
        chunk_index=metadata.chunk_index,
        total_chunks=metadata.total_chunks,
        function_name=metadata.function_name,
        class_name=metadata.class_name,
        line_start=metadata.line_start,
        line_end=metadata.line_end,
        imports=metadata.imports,
        module=metadata.module,
        risk_area=metadata.risk_area,
        has_static_issues=metadata.has_static_issues,
        token_count=metadata.token_count,
        chunk_text=chunk.content,
    )
