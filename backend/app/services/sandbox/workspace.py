"""Shared sandbox size validation and safe cleanup operations."""

import shutil
from pathlib import Path


class SandboxError(RuntimeError):
    """Raised when a sandbox operation is unsafe or invalid."""


def validate_repository_size(
    sandbox_path: Path,
    *,
    max_size_bytes: int,
) -> None:
    """Reject a repository that exceeds the configured sandbox size."""

    total_size = sum(
        file_path.stat().st_size
        for file_path in sandbox_path.rglob("*")
        if file_path.is_file()
    )
    if total_size <= max_size_bytes:
        return

    size_mb = total_size / 1024 / 1024
    max_size_mb = max_size_bytes / 1024 / 1024
    raise SandboxError(
        f"Repository is too large: {size_mb:.1f}MB exceeds {max_size_mb:.0f}MB"
    )


def cleanup_sandbox(sandbox_path: Path, sandbox_root: Path) -> None:
    """Remove one sandbox after confirming it is below the sandbox root."""

    resolved_root = sandbox_root.resolve()
    resolved_path = sandbox_path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise SandboxError(f"Refusing to cleanup unsafe path: {resolved_path}")

    if resolved_path.exists():
        shutil.rmtree(resolved_path)
