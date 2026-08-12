"""Ephemeral container execution for untrusted fix verification commands."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import Settings

MAX_COMMAND_OUTPUT_LENGTH = 4_000
EXECUTOR_UNAVAILABLE_EXIT_CODE = 125
COMMAND_TIMEOUT_EXIT_CODE = 124
CONTAINER_WORKSPACE = "/workspace"
CONTAINER_TMP_SIZE = "256m"
# This path exists only inside the disposable container, not on the host.
CONTAINER_TMP_MOUNT = f"/tmp:rw,noexec,nosuid,size={CONTAINER_TMP_SIZE}"  # noqa: S108


class FixCommandExecutor(Protocol):
    """Run a fixed argv inside an isolated fix workspace."""

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        """Return exit code, stdout, stderr, and duration."""


@dataclass(frozen=True, slots=True)
class DockerFixCommandExecutor:
    """Run each command in a disposable, resource-limited Docker container."""

    docker_executable: str | None
    image: str
    sandbox_root: Path
    workspace_volume: str | None
    network: str
    memory_mb: int
    cpu_limit: float
    pids_limit: int

    @classmethod
    def from_settings(cls, settings: Settings) -> DockerFixCommandExecutor:
        """Build the executor exclusively from validated application settings."""

        return cls(
            docker_executable=settings.fix_executor_docker_executable,
            image=settings.fix_executor_image,
            sandbox_root=Path(settings.sandbox_root).resolve(),
            workspace_volume=settings.fix_executor_workspace_volume,
            network=settings.fix_executor_network,
            memory_mb=settings.fix_executor_memory_mb,
            cpu_limit=settings.fix_executor_cpu_limit,
            pids_limit=settings.fix_executor_pids_limit,
        )

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
    ) -> tuple[int, str, str, int]:
        """Run one argv without a shell or access to worker secrets."""

        started_at = time.perf_counter()
        if self.docker_executable is None:
            return self._unavailable(started_at, "Docker executable is unavailable")
        if not command:
            return self._unavailable(started_at, "Executor command is empty")

        resolved_cwd = cwd.resolve()
        if not resolved_cwd.is_relative_to(self.sandbox_root):
            return self._unavailable(
                started_at,
                "Executor working directory is outside the configured sandbox",
            )

        container_name = f"repoguard-fix-{uuid4().hex}"
        docker_command = self._docker_command(
            container_name=container_name,
            cwd=resolved_cwd,
            command=command,
        )
        try:
            completed = subprocess.run(  # noqa: S603 - fixed Docker argv, no shell
                docker_command,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return self._unavailable(started_at, "Docker executable is unavailable")
        except subprocess.TimeoutExpired as error:
            self._remove_container(container_name)
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            return (
                COMMAND_TIMEOUT_EXIT_CODE,
                _truncate(stdout),
                _truncate(stderr or f"Timed out after {timeout_seconds}s"),
                _duration_ms(started_at),
            )

        return (
            int(completed.returncode),
            _truncate(completed.stdout),
            _truncate(completed.stderr),
            _duration_ms(started_at),
        )

    def _docker_command(
        self,
        *,
        container_name: str,
        cwd: Path,
        command: list[str],
    ) -> list[str]:
        relative_cwd = cwd.relative_to(self.sandbox_root).as_posix()
        container_cwd = (
            CONTAINER_WORKSPACE
            if relative_cwd == "."
            else f"{CONTAINER_WORKSPACE}/{relative_cwd}"
        )
        mount = (
            f"type=volume,src={self.workspace_volume},dst={CONTAINER_WORKSPACE}"
            if self.workspace_volume
            else f"type=bind,src={self.sandbox_root},dst={CONTAINER_WORKSPACE}"
        )
        return [
            self.docker_executable or "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            container_name,
            "--network",
            self.network,
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpu_limit),
            "--pids-limit",
            str(self.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            CONTAINER_TMP_MOUNT,
            "--mount",
            mount,
            "--workdir",
            container_cwd,
            "--env",
            "CI=1",
            "--env",
            "GIT_TERMINAL_PROMPT=0",
            "--env",
            "HOME=/tmp",
            self.image,
            *command,
        ]

    def _remove_container(self, container_name: str) -> None:
        if self.docker_executable is None:
            return
        subprocess.run(  # noqa: S603 - fixed cleanup argv, no shell
            [self.docker_executable, "rm", "--force", container_name],
            capture_output=True,
            check=False,
            timeout=10,
        )

    @staticmethod
    def _unavailable(
        started_at: float,
        reason: str,
    ) -> tuple[int, str, str, int]:
        return EXECUTOR_UNAVAILABLE_EXIT_CODE, "", reason, _duration_ms(started_at)


def _duration_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1_000)


def _truncate(output: str) -> str:
    return output[-MAX_COMMAND_OUTPUT_LENGTH:]
