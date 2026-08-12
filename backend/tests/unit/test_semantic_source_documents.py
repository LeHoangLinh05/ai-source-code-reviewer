"""Tests for source selection used by semantic review."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.services.code_indexing.documents import (
    build_source_chunk_documents,
    should_index_semantic_file,
)


@pytest.mark.parametrize(
    "file_path",
    [
        "frontend/package-lock.json",
        "frontend/pnpm-lock.yaml",
        "frontend/.next/server/app.js",
        "coverage/report.js",
        "vendor/library.py",
        "frontend/app.min.js",
        "frontend/app.js.map",
    ],
)
def test_semantic_index_excludes_generated_and_lock_files(file_path: str) -> None:
    assert not should_index_semantic_file(Path(file_path))


def test_semantic_index_keeps_runtime_source_and_manifests() -> None:
    assert should_index_semantic_file(Path("backend/app/api/auth.py"))
    assert should_index_semantic_file(Path("frontend/package.json"))


def test_semantic_index_drops_empty_python_chunks(tmp_path: Path) -> None:
    source_path = tmp_path / "empty.py"
    source_path.write_text("\n\n", encoding="utf-8")

    documents = build_source_chunk_documents(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        filtered_files=[source_path],
        issues=[],
    )

    assert documents == []
