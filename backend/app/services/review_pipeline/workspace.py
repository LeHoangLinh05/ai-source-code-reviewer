"""Repository sandbox lifecycle operations for review pipelines."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.core.config import build_git_subprocess_env, get_git_executable
from app.models.review_job import ReviewJob
from app.services.review_pipeline.errors import ReviewPipelineError

CLONE_TIMEOUT_SECONDS = 120


def clone_repository(review_job: ReviewJob, sandbox_path: Path) -> None:
    """Clone a repository into its sandbox path."""

    cleanup_sandbox(sandbox_path, sandbox_path.parent)
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    git_executable = _get_required_git_executable()
    repository_url = review_job.repository.url
    branch = review_job.branch or review_job.repository.default_branch
    command = [
        git_executable,
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        "--single-branch",
        repository_url,
        str(sandbox_path),
    ]
    completed_process = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=build_git_subprocess_env(),
        text=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if completed_process.returncode != 0:
        detail = completed_process.stderr.strip() or completed_process.stdout.strip()
        raise ReviewPipelineError(f"Git clone failed: {detail}")


def validate_repo_size(sandbox_path: Path, *, max_size_bytes: int) -> None:
    """Reject cloned repositories that exceed the configured sandbox size."""

    total_size = sum(
        file_path.stat().st_size
        for file_path in sandbox_path.rglob("*")
        if file_path.is_file()
    )
    if total_size <= max_size_bytes:
        return

    size_mb = total_size / 1024 / 1024
    max_size_mb = max_size_bytes / 1024 / 1024
    raise ReviewPipelineError(
        f"Repository is too large: {size_mb:.1f}MB exceeds {max_size_mb:.0f}MB"
    )


def get_commit_sha(sandbox_path: Path) -> str:
    """Return the current cloned commit SHA."""

    git_executable = _get_required_git_executable()
    completed_process = subprocess.run(
        [git_executable, "rev-parse", "HEAD"],
        cwd=sandbox_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed_process.returncode != 0:
        raise ReviewPipelineError("Unable to read cloned repository commit SHA")

    return completed_process.stdout.strip()


def _get_required_git_executable() -> str:
    try:
        return get_git_executable()
    except RuntimeError as error:
        raise ReviewPipelineError(str(error)) from error


def cleanup_sandbox(sandbox_path: Path, sandbox_root: Path) -> None:
    """Remove one sandbox path after confirming it is under the sandbox root."""

    resolved_root = sandbox_root.resolve()
    resolved_path = sandbox_path.resolve()
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ReviewPipelineError(f"Refusing to cleanup unsafe path: {resolved_path}")

    if resolved_path.exists():
        shutil.rmtree(resolved_path)
