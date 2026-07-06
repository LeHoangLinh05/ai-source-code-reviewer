"""Ruff static analysis adapter for Python lint findings."""

from pathlib import Path
import json
from typing import Any

from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.static_analysis.base import StaticAnalysisRun, run_static_command
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue


def run_ruff(
    sandbox_path: Path,
    python_files: list[Path],
    *,
    timeout_seconds: int,
) -> StaticAnalysisRun:
    """Run Ruff against filtered Python files."""

    if not python_files:
        return StaticAnalysisRun("ruff", "python", 0, "[]", "", 0, [])

    command = [
        "ruff",
        "check",
        "--output-format",
        "json",
        *[
            to_relative_posix_path(file_path, sandbox_path)
            for file_path in python_files
        ],
    ]
    exit_code, stdout, stderr, duration_ms = run_static_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
    )
    return StaticAnalysisRun(
        tool="ruff",
        language="python",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        issues=ruff_to_normalized(stdout, sandbox_path),
    )


def ruff_to_normalized(
    stdout: str,
    sandbox_path: Path | None = None,
) -> list[NormalizedIssue]:
    """Convert Ruff JSON output into normalized issues."""

    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []

    issues: list[NormalizedIssue] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        location = item.get("location")
        end_location = item.get("end_location")
        line_start = _get_line_number(location)
        issues.append(
            NormalizedIssue(
                file_path=_normalize_file_path(
                    str(item.get("filename", "")), sandbox_path
                ),
                line_start=line_start,
                line_end=_get_line_number(end_location) or line_start,
                severity=IssueSeverity.LOW,
                category=IssueCategory.STYLE,
                title=f"Ruff {item.get('code', 'finding')}",
                description=str(item.get("message", "Ruff reported a lint finding.")),
                suggestion=_get_fix_message(item),
                source=IssueSource.RUFF,
                confidence=0.9,
                raw_output=dict(item),
            )
        )

    return issues


def _normalize_file_path(file_path: str, sandbox_path: Path | None) -> str:
    if sandbox_path is None:
        return file_path

    path = Path(file_path)
    try:
        return path.resolve().relative_to(sandbox_path.resolve()).as_posix()
    except ValueError:
        return file_path


def _get_line_number(location: object) -> int | None:
    if not isinstance(location, dict):
        return None

    row = location.get("row")
    return row if isinstance(row, int) and row >= 1 else None


def _get_fix_message(item: dict[str, Any]) -> str | None:
    fix = item.get("fix")
    if not isinstance(fix, dict):
        return None

    message = fix.get("message")
    return str(message) if message is not None else None
