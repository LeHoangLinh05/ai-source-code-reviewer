"""Manage repository workspaces for review pipelines."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import build_git_subprocess_env, get_git_executable
from app.models.review_job import ReviewJob
from app.services.review_jobs.pipeline.errors import ReviewPipelineError
from app.services.sandbox.workspace import cleanup_sandbox

CLONE_TIMEOUT_SECONDS = 120
GIT_METADATA_TIMEOUT_SECONDS = 10
REPOSITORY_UNAVAILABLE_MESSAGE = "Repository does not exist or is private."


def clone_repository(review_job: ReviewJob, sandbox_path: Path) -> None:
    """Clone a repository into its sandbox path."""

    cleanup_sandbox(sandbox_path, sandbox_path.parent)
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    git_executable = _get_required_git_executable()
    repository_url = review_job.repository.url
    branch = review_job.branch or review_job.repository.default_branch
    command = _build_clone_command(
        git_executable=git_executable,
        repository_url=repository_url,
        branch=branch,
        sandbox_path=sandbox_path,
        commit_sha=review_job.commit_sha,
    )
    try:
        _run_git_command(command, error_prefix="Git clone failed")
    except ReviewPipelineError as error:
        raise ReviewPipelineError(REPOSITORY_UNAVAILABLE_MESSAGE) from error

    if review_job.commit_sha is not None:
        _checkout_commit(git_executable, sandbox_path, review_job.commit_sha)


def _build_clone_command(
    *,
    git_executable: str,
    repository_url: str,
    branch: str,
    sandbox_path: Path,
    commit_sha: str | None,
) -> list[str]:
    command = [
        git_executable,
        "clone",
    ]
    if commit_sha is None:
        command.extend(("--depth", "1"))
    else:
        command.append("--no-checkout")

    command.extend(
        ("--branch", branch, "--single-branch", repository_url, str(sandbox_path))
    )
    return command


def _checkout_commit(
    git_executable: str,
    sandbox_path: Path,
    commit_sha: str,
) -> None:
    command = [git_executable, "checkout", "--detach", commit_sha]
    _run_git_command(
        command,
        cwd=sandbox_path,
        error_prefix=f"Git checkout failed for commit {commit_sha}",
    )


def _run_git_command(
    command: list[str],
    *,
    error_prefix: str,
    cwd: Path | None = None,
) -> None:
    completed_process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        env=build_git_subprocess_env(),
        text=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if completed_process.returncode != 0:
        detail = completed_process.stderr.strip() or completed_process.stdout.strip()
        raise ReviewPipelineError(f"{error_prefix}: {detail}")


def get_commit_sha(sandbox_path: Path) -> str:
    """Return the current cloned commit SHA."""

    git_executable = _get_required_git_executable()
    completed_process = subprocess.run(
        [git_executable, "rev-parse", "HEAD"],
        cwd=sandbox_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=GIT_METADATA_TIMEOUT_SECONDS,
    )
    if completed_process.returncode != 0:
        raise ReviewPipelineError("Unable to read cloned repository commit SHA")

    return completed_process.stdout.strip()


def _get_required_git_executable() -> str:
    try:
        return get_git_executable()
    except RuntimeError as error:
        raise ReviewPipelineError(str(error)) from error
