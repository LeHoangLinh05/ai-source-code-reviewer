"""Tests for review pipeline Git workspace preparation."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.models.review_job import ReviewJob
from app.services.review_pipeline import workspace
from app.services.review_pipeline.errors import ReviewPipelineError


def test_clone_repository_uses_shallow_clone_for_branch_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands = record_git_commands(monkeypatch)

    workspace.clone_repository(build_review_job(commit_sha=None), tmp_path / "repo")

    assert len(commands) == 1
    assert commands[0][0] == [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        "main",
        "--single-branch",
        "https://example.com/repository.git",
        str(tmp_path / "repo"),
    ]


def test_clone_repository_checks_out_pinned_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commit_sha = "a" * 40
    commands = record_git_commands(monkeypatch)

    workspace.clone_repository(
        build_review_job(commit_sha=commit_sha),
        tmp_path / "repo",
    )

    assert len(commands) == 2
    clone_command, clone_cwd = commands[0]
    assert "--depth" not in clone_command
    assert "--no-checkout" in clone_command
    assert clone_cwd is None
    assert commands[1] == (
        ["git", "checkout", "--detach", commit_sha],
        tmp_path / "repo",
    )


def test_clone_repository_reports_missing_or_private_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(workspace, "_get_required_git_executable", lambda: "git")

    def fail_clone(
        command: list[str],
        *,
        error_prefix: str,
        cwd: Path | None = None,
    ) -> None:
        del command, error_prefix, cwd
        raise ReviewPipelineError("Git clone failed: terminal prompts disabled")

    monkeypatch.setattr(workspace, "_run_git_command", fail_clone)

    with pytest.raises(
        ReviewPipelineError,
        match=r"^Repository does not exist or is private\.$",
    ):
        workspace.clone_repository(build_review_job(commit_sha=None), tmp_path / "repo")


def record_git_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[list[str], Path | None]]:
    commands: list[tuple[list[str], Path | None]] = []

    def record_command(
        command: list[str],
        *,
        error_prefix: str,
        cwd: Path | None = None,
    ) -> None:
        del error_prefix
        commands.append((command, cwd))

    monkeypatch.setattr(workspace, "_get_required_git_executable", lambda: "git")
    monkeypatch.setattr(workspace, "_run_git_command", record_command)
    return commands


def build_review_job(*, commit_sha: str | None) -> ReviewJob:
    return cast(
        ReviewJob,
        SimpleNamespace(
            branch="main",
            commit_sha=commit_sha,
            repository=SimpleNamespace(
                default_branch="main",
                url="https://example.com/repository.git",
            ),
        ),
    )
