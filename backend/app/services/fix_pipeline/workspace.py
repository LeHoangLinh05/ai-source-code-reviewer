"""Git workspace operations for fix pipelines."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from uuid import UUID

from app.core.config import build_git_subprocess_env, get_git_executable
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.review_pipeline.workspace import cleanup_sandbox

GIT_TIMEOUT_SECONDS = 120
GIT_SHORT_TIMEOUT_SECONDS = 20
FIX_BRANCH_PREFIX = "repoguard/fix"
PUBLISH_COMMIT_AUTHOR_NAME = "RepoGuard AI"
PUBLISH_COMMIT_AUTHOR_EMAIL = "repoguard-ai@example.invalid"


def build_fix_branch(fix_job_id: UUID) -> str:
    """Return a stable local branch name for one fix job."""

    return f"{FIX_BRANCH_PREFIX}/{fix_job_id}"


def prepare_fix_workspace(
    *,
    repository_url: str,
    clone_url: str | None = None,
    target_branch: str,
    base_commit_sha: str,
    fix_branch: str,
    sandbox_path: Path,
    sandbox_root: Path,
) -> None:
    """Clone a branch, checkout the reviewed commit, and create a fix branch."""

    cleanup_sandbox(sandbox_path, sandbox_root)
    sandbox_path.parent.mkdir(parents=True, exist_ok=True)
    git_executable = _get_required_git_executable()
    _run_git(
        [
            git_executable,
            "clone",
            "--branch",
            target_branch,
            "--single-branch",
            clone_url or repository_url,
            str(sandbox_path),
        ],
        timeout_seconds=GIT_TIMEOUT_SECONDS,
    )
    _run_git(
        [git_executable, "checkout", base_commit_sha],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    _run_git(
        [git_executable, "checkout", "-B", fix_branch],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )


def get_git_diff(sandbox_path: Path) -> str:
    """Return the current workspace diff."""

    git_executable = _get_required_git_executable()
    return _run_git(
        [git_executable, "diff", "--binary"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )


def get_changed_files(sandbox_path: Path) -> list[str]:
    """Return changed file paths from the current workspace diff."""

    git_executable = _get_required_git_executable()
    output = _run_git(
        [git_executable, "diff", "--name-only"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def restore_index_files(sandbox_path: Path, file_paths: list[str]) -> None:
    """Restore tracked files that an untrusted verification test modified."""

    git_executable = _get_required_git_executable()
    for file_path in file_paths:
        resolve_repo_file(sandbox_path, file_path)
        _run_git(
            [git_executable, "checkout-index", "--force", "--", file_path],
            cwd=sandbox_path,
            timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
        )


def apply_git_diff(
    *,
    sandbox_path: Path,
    diff: str,
) -> None:
    """Apply a stored unified diff to a publish workspace."""

    if not diff.strip():
        raise FixPipelineError("Stored fix diff is empty")

    git_executable = _get_required_git_executable()
    _run_git(
        [git_executable, "apply", "--binary"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
        input_text=diff,
    )


def create_git_commit(
    *,
    sandbox_path: Path,
    message: str,
) -> str:
    """Create a commit from the current workspace changes and return its SHA."""

    git_executable = _get_required_git_executable()
    _run_git(
        [git_executable, "config", "user.name", PUBLISH_COMMIT_AUTHOR_NAME],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    _run_git(
        [git_executable, "config", "user.email", PUBLISH_COMMIT_AUTHOR_EMAIL],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    if not has_workspace_changes(sandbox_path):
        raise FixPipelineError("Stored fix diff produced no workspace changes")

    _run_git(
        [git_executable, "add", "-A"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    _run_git(
        [git_executable, "commit", "-m", message],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    return get_current_commit_sha(sandbox_path)


def has_workspace_changes(sandbox_path: Path) -> bool:
    """Return whether the workspace has uncommitted changes."""

    git_executable = _get_required_git_executable()
    output = _run_git(
        [git_executable, "status", "--porcelain"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    return bool(output.strip())


def get_current_commit_sha(sandbox_path: Path) -> str:
    """Return the current workspace HEAD commit SHA."""

    git_executable = _get_required_git_executable()
    output = _run_git(
        [git_executable, "rev-parse", "HEAD"],
        cwd=sandbox_path,
        timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
    )
    return output.strip()


def set_git_remote_url(
    *,
    sandbox_path: Path,
    remote_name: str,
    remote_url: str,
) -> None:
    """Set or add a git remote URL for publishing."""

    git_executable = _get_required_git_executable()
    try:
        _run_git(
            [git_executable, "remote", "set-url", remote_name, remote_url],
            cwd=sandbox_path,
            timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
        )
    except FixPipelineError:
        _run_git(
            [git_executable, "remote", "add", remote_name, remote_url],
            cwd=sandbox_path,
            timeout_seconds=GIT_SHORT_TIMEOUT_SECONDS,
        )


def push_git_branch(
    *,
    sandbox_path: Path,
    branch_name: str,
    remote_name: str,
) -> None:
    """Push the current HEAD to a remote branch."""

    git_executable = _get_required_git_executable()
    _run_git(
        [git_executable, "push", remote_name, f"HEAD:{branch_name}"],
        cwd=sandbox_path,
        timeout_seconds=GIT_TIMEOUT_SECONDS,
    )


def resolve_repo_file(sandbox_path: Path, file_path: str) -> Path:
    """Return a safe absolute path for a repository-relative file."""

    normalized_path = Path(file_path.replace("\\", "/"))
    if normalized_path.is_absolute() or ".." in normalized_path.parts:
        raise FixPipelineError(f"Unsafe issue file path: {file_path}")

    resolved_root = sandbox_path.resolve()
    resolved_path = (sandbox_path / normalized_path).resolve()
    if resolved_root not in resolved_path.parents and resolved_path != resolved_root:
        raise FixPipelineError(f"Unsafe issue file path: {file_path}")

    return resolved_path


def _get_required_git_executable() -> str:
    try:
        return get_git_executable()
    except RuntimeError as error:
        raise FixPipelineError(str(error)) from error


def _run_git(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int,
    input_text: str | None = None,
) -> str:
    try:
        completed_process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            env=build_git_subprocess_env(),
            input=input_text,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise FixPipelineError(
            f"Git command timed out after {timeout_seconds}s"
        ) from error

    if completed_process.returncode == 0:
        return completed_process.stdout

    detail = completed_process.stderr.strip() or completed_process.stdout.strip()
    raise FixPipelineError(f"Git command failed: {_mask_sensitive_git_output(detail)}")


def _mask_sensitive_git_output(output: str) -> str:
    return re.sub(r"(https://[^:\s/]+:)[^@\s]+@", r"\1***@", output)
