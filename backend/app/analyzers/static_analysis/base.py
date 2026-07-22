"""Shared static analysis adapter contracts and result shapes."""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.schemas.normalized_issue import NormalizedIssue


@dataclass(frozen=True, slots=True)
class StaticAnalysisRun:
    """Raw subprocess result plus normalized issues for one analyzer."""

    tool: str
    language: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    issues: list[NormalizedIssue]


def run_static_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> tuple[int, str, str, int]:
    """Run one analyzer command and return captured output."""

    started_at = time.perf_counter()
    try:
        completed_process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return 127, "", str(error), duration_ms
    except subprocess.TimeoutExpired as error:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else ""
        return 124, stdout, stderr or f"Timed out after {timeout_seconds}s", duration_ms

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    return (
        int(completed_process.returncode),
        completed_process.stdout,
        completed_process.stderr,
        duration_ms,
    )


def filter_files_by_suffix(files: list[Path], suffixes: set[str]) -> list[Path]:
    """Return analyzer input files matching the requested suffixes."""

    return [file_path for file_path in files if file_path.suffix.lower() in suffixes]
