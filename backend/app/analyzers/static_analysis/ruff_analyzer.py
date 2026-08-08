"""Ruff static analysis adapter for Python lint findings."""

import json
from pathlib import Path
from typing import Any

from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.static_analysis.base import StaticAnalysisRun, run_static_command
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue

RUFF_SOURCE_LABEL = "Ruff"
DEFAULT_RUFF_DESCRIPTION = "Ruff reported a lint finding."


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

        rule_code = _get_rule_code(item)
        description = _get_description(item)
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
                title=_build_title(rule_code, description),
                description=description,
                suggestion=_get_fix_message(item),
                source=IssueSource.RUFF,
                confidence=0.9,
                raw_output=dict(item),
            )
        )

    return issues


def _get_rule_code(item: dict[str, object]) -> str:
    code = item.get("code")
    if code is None:
        return "finding"

    rule_code = str(code).strip()
    return rule_code or "finding"


def _get_description(item: dict[str, object]) -> str:
    message = item.get("message")
    if message is None:
        return DEFAULT_RUFF_DESCRIPTION

    description = str(message).strip()
    return description or DEFAULT_RUFF_DESCRIPTION


def _build_title(rule_code: str, description: str) -> str:
    if rule_code == "finding":
        return f"{RUFF_SOURCE_LABEL} finding"

    if description == DEFAULT_RUFF_DESCRIPTION:
        return f"{RUFF_SOURCE_LABEL} {rule_code}"

    return f"{RUFF_SOURCE_LABEL} {rule_code}: {description}"


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
