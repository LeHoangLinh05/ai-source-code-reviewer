"""Tests for isolated fix command execution."""

from pathlib import Path
from types import SimpleNamespace

from app.services.fix_pipeline import execution
from app.services.fix_pipeline.execution import DockerFixCommandExecutor


def test_docker_executor_uses_ephemeral_restricted_container(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "fixes" / "job"
    project_path.mkdir(parents=True)
    captured_commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        del kwargs
        captured_commands.append(command)
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(execution.subprocess, "run", run)
    executor = _executor(tmp_path)

    result = executor.run(
        ["pytest", "-q"],
        cwd=project_path,
        timeout_seconds=30,
    )

    assert result[:3] == (0, "passed", "")
    command = captured_commands[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in command
    assert "--cap-drop" in command
    assert "no-new-privileges" in command
    assert "--pids-limit" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert command[-3:] == ["repoguard-fix-executor:test", "pytest", "-q"]
    assert not any("OPENAI" in value or "DATABASE_URL" in value for value in command)


def test_docker_executor_fails_closed_when_docker_is_unavailable(
    tmp_path: Path,
) -> None:
    executor = DockerFixCommandExecutor(
        docker_executable=None,
        image="repoguard-fix-executor:test",
        sandbox_root=tmp_path,
        workspace_volume=None,
        network="none",
        memory_mb=512,
        cpu_limit=1.0,
        pids_limit=128,
    )

    exit_code, _stdout, stderr, _duration_ms = executor.run(
        ["pytest"],
        cwd=tmp_path,
        timeout_seconds=10,
    )

    assert exit_code == execution.EXECUTOR_UNAVAILABLE_EXIT_CODE
    assert "unavailable" in stderr


def test_docker_executor_rejects_working_directory_outside_sandbox(
    tmp_path: Path,
) -> None:
    sandbox_path = tmp_path / "sandbox"
    outside_path = tmp_path / "outside"
    sandbox_path.mkdir()
    outside_path.mkdir()
    executor = _executor(sandbox_path)

    exit_code, _stdout, stderr, _duration_ms = executor.run(
        ["pytest"],
        cwd=outside_path,
        timeout_seconds=10,
    )

    assert exit_code == execution.EXECUTOR_UNAVAILABLE_EXIT_CODE
    assert "outside" in stderr


def _executor(sandbox_root: Path) -> DockerFixCommandExecutor:
    return DockerFixCommandExecutor(
        docker_executable="docker",
        image="repoguard-fix-executor:test",
        sandbox_root=sandbox_root,
        workspace_volume=None,
        network="none",
        memory_mb=512,
        cpu_limit=1.0,
        pids_limit=128,
    )
