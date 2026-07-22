"""ESLint static analysis adapter for JavaScript and TypeScript findings."""

import json
from pathlib import Path

from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.static_analysis.base import StaticAnalysisRun, run_static_command
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue

ESLINT_ERROR_SEVERITY = 2


def run_eslint(
    sandbox_path: Path,
    javascript_files: list[Path],
    *,
    config_path: Path,
    timeout_seconds: int,
) -> StaticAnalysisRun:
    """Run ESLint against filtered JavaScript and TypeScript files."""

    if not javascript_files:
        return StaticAnalysisRun("eslint", "javascript", 0, "[]", "", 0, [])

    command = [
        "eslint",
        "--config",
        str(config_path),
        "--format",
        "json",
        "--no-error-on-unmatched-pattern",
        *[
            to_relative_posix_path(file_path, sandbox_path)
            for file_path in javascript_files
        ],
    ]
    exit_code, stdout, stderr, duration_ms = run_static_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
    )
    return StaticAnalysisRun(
        tool="eslint",
        language="javascript",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        issues=eslint_to_normalized(stdout, sandbox_path),
    )


def eslint_to_normalized(
    stdout: str,
    sandbox_path: Path | None = None,
) -> list[NormalizedIssue]:
    """Convert ESLint JSON output into normalized issues."""

    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, list):
        return []

    issues: list[NormalizedIssue] = []
    for file_result in payload:
        if not isinstance(file_result, dict):
            continue

        file_path = _normalize_file_path(
            str(file_result.get("filePath", "")), sandbox_path
        )
        messages = file_result.get("messages")
        if not isinstance(messages, list):
            continue

        for message in messages:
            if not isinstance(message, dict):
                continue

            line = message.get("line")
            line_start = line if isinstance(line, int) and line >= 1 else None
            issues.append(
                NormalizedIssue(
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_start,
                    severity=_eslint_severity(message.get("severity")),
                    category=IssueCategory.STYLE,
                    title=f"ESLint {message.get('ruleId') or 'finding'}",
                    description=str(
                        message.get("message", "ESLint reported a finding.")
                    ),
                    suggestion="Review the ESLint rule guidance and update the file.",
                    source=IssueSource.ESLINT,
                    confidence=0.85,
                    raw_output=dict(message),
                )
            )

    return issues


def _eslint_severity(severity: object) -> IssueSeverity:
    return (
        IssueSeverity.MEDIUM if severity == ESLINT_ERROR_SEVERITY else IssueSeverity.LOW
    )


def _normalize_file_path(file_path: str, sandbox_path: Path | None) -> str:
    if sandbox_path is None:
        return file_path

    path = Path(file_path)
    try:
        return path.resolve().relative_to(sandbox_path.resolve()).as_posix()
    except ValueError:
        return file_path
