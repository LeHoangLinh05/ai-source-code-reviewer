"""Bandit static analysis adapter for Python security findings."""

from pathlib import Path
import json

from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.static_analysis.base import StaticAnalysisRun, run_static_command
from app.models.review_issue import IssueCategory, IssueSeverity, IssueSource
from app.schemas.normalized_issue import NormalizedIssue

BANDIT_SEVERITY_MAP = {
    "HIGH": IssueSeverity.HIGH,
    "MEDIUM": IssueSeverity.MEDIUM,
    "LOW": IssueSeverity.LOW,
}


def run_bandit(
    sandbox_path: Path,
    python_files: list[Path],
    *,
    timeout_seconds: int,
) -> StaticAnalysisRun:
    """Run Bandit against filtered Python files."""

    if not python_files:
        return StaticAnalysisRun("bandit", "python", 0, "{}", "", 0, [])

    command = [
        "bandit",
        "-f",
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
        tool="bandit",
        language="python",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        issues=bandit_to_normalized(stdout, sandbox_path),
    )


def bandit_to_normalized(
    stdout: str,
    sandbox_path: Path | None = None,
) -> list[NormalizedIssue]:
    """Convert Bandit JSON output into normalized issues."""

    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []

    issues: list[NormalizedIssue] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        severity = BANDIT_SEVERITY_MAP.get(
            str(item.get("issue_severity", "")).upper(),
            IssueSeverity.LOW,
        )
        line_number = item.get("line_number")
        line_start = (
            line_number if isinstance(line_number, int) and line_number else None
        )
        issues.append(
            NormalizedIssue(
                file_path=_normalize_file_path(
                    str(item.get("filename", "")), sandbox_path
                ),
                line_start=line_start,
                line_end=line_start,
                severity=severity,
                category=IssueCategory.SECURITY,
                title=str(
                    item.get("test_name") or item.get("test_id") or "Bandit finding"
                ),
                description=str(item.get("issue_text", "Bandit reported a finding.")),
                suggestion="Review the security finding and apply Bandit guidance.",
                source=IssueSource.BANDIT,
                confidence=_bandit_confidence_to_float(item.get("issue_confidence")),
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


def _bandit_confidence_to_float(confidence: object) -> float:
    normalized = str(confidence or "").upper()
    if normalized == "HIGH":
        return 0.9
    if normalized == "MEDIUM":
        return 0.75
    return 0.6
