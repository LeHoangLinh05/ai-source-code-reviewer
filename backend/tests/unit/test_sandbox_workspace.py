"""Tests for shared sandbox safety operations."""

from pathlib import Path

import pytest

from app.services.sandbox.workspace import (
    SandboxError,
    cleanup_sandbox,
    validate_repository_size,
)


def test_validate_repository_size_accepts_repository_within_limit(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "app.py").write_text("value = 1\n", encoding="utf-8")

    validate_repository_size(repository_path, max_size_bytes=100)


def test_validate_repository_size_rejects_repository_over_limit(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "large.txt").write_text("x" * 101, encoding="utf-8")

    with pytest.raises(SandboxError, match="Repository is too large"):
        validate_repository_size(repository_path, max_size_bytes=100)


def test_cleanup_sandbox_removes_only_child_path(tmp_path: Path) -> None:
    sandbox_path = tmp_path / "job"
    sandbox_path.mkdir()
    (sandbox_path / "artifact.txt").write_text("artifact", encoding="utf-8")

    cleanup_sandbox(sandbox_path, tmp_path)

    assert not sandbox_path.exists()


def test_cleanup_sandbox_rejects_root_path(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="Refusing to cleanup unsafe path"):
        cleanup_sandbox(tmp_path, tmp_path)
