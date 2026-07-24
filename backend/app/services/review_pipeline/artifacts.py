"""Static-analysis execution and persisted review artifact construction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.ai.roadmap.selection import parse_roadmap_profile
from app.analyzers.file_filter import to_relative_posix_path
from app.analyzers.static_analysis.bandit_analyzer import run_bandit
from app.analyzers.static_analysis.base import StaticAnalysisRun, filter_files_by_suffix
from app.analyzers.static_analysis.eslint_analyzer import run_eslint
from app.analyzers.static_analysis.ruff_analyzer import run_ruff
from app.analyzers.structure_analyzer import LANGUAGE_BY_EXTENSION
from app.core.config import BACKEND_DIR
from app.schemas.mongodb import (
    FileTreeEntry,
    ParsedStaticIssue,
    RawStaticAnalysisOutputDocument,
)
from app.schemas.normalized_issue import NormalizedIssue
from app.services.review_pipeline.errors import ReviewPipelineError


def build_static_analysis_runs(
    sandbox_path: Path,
    filtered_files: list[Path],
    *,
    timeout_seconds: int,
) -> list[StaticAnalysisRun]:
    """Run supported static analyzers for matching filtered files."""

    python_files = filter_files_by_suffix(filtered_files, {".py"})
    javascript_files = filter_files_by_suffix(
        filtered_files,
        {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
    )
    return [
        run_ruff(sandbox_path, python_files, timeout_seconds=timeout_seconds),
        run_bandit(sandbox_path, python_files, timeout_seconds=timeout_seconds),
        run_eslint(
            sandbox_path,
            javascript_files,
            config_path=BACKEND_DIR / "eslint.config.mjs",
            timeout_seconds=timeout_seconds,
        ),
    ]


def get_rule_profile(options: dict[str, object] | None) -> dict[str, object] | None:
    """Return the explicit roadmap profile; null means roadmap is disabled."""

    try:
        profile = parse_roadmap_profile(options)
    except ValueError as error:
        raise ReviewPipelineError(str(error)) from error
    if profile is None:
        return None

    output: dict[str, object] = {"id": profile.profile_id}
    if profile.weeks_included is not None:
        output["weeks_included"] = list(profile.weeks_included)
    return output


def build_raw_static_document(
    job_id: UUID,
    analysis_run: StaticAnalysisRun,
) -> RawStaticAnalysisOutputDocument:
    """Build the MongoDB raw output document for one analyzer run."""

    return RawStaticAnalysisOutputDocument(
        job_id=job_id,
        tool=analysis_run.tool,
        language=analysis_run.language,
        ran_at=datetime.now(UTC),
        exit_code=analysis_run.exit_code,
        stdout=analysis_run.stdout,
        stderr=analysis_run.stderr,
        duration_ms=analysis_run.duration_ms,
        parsed_issues=[
            ParsedStaticIssue(
                file_path=issue.file_path or "",
                line_start=issue.line_start,
                rule_id=get_rule_id(issue.raw_output),
                message=issue.description,
                severity=issue.severity.value,
                category=issue.category.value,
            )
            for issue in analysis_run.issues
        ],
    )


def get_rule_id(raw_output: dict[str, object] | None) -> str | None:
    """Extract a static analyzer rule id from raw output when available."""

    if raw_output is None:
        return None

    for key in ("code", "test_id", "ruleId"):
        value = raw_output.get(key)
        if value is not None:
            return str(value)

    return None


def attach_source_context(
    issues: list[NormalizedIssue],
    sandbox_path: Path,
    *,
    context_radius: int = 2,
) -> None:
    """Attach nearby source lines to issue raw output before sandbox cleanup."""

    sandbox_root = sandbox_path.resolve()
    for issue in issues:
        if issue.file_path is None or issue.line_start is None:
            continue

        source_path = (sandbox_root / issue.file_path).resolve()
        try:
            source_path.relative_to(sandbox_root)
        except ValueError:
            continue

        if not source_path.is_file():
            continue

        lines = source_path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines()
        if not lines:
            continue

        first_line = max(1, issue.line_start - context_radius)
        last_line = min(
            len(lines),
            (issue.line_end or issue.line_start) + context_radius,
        )
        context_lines = lines[first_line - 1 : last_line]
        raw_output = dict(issue.raw_output or {})
        raw_output["source_context"] = {
            "start_line": first_line,
            "lines": context_lines,
        }
        issue.raw_output = raw_output


def build_flat_file_tree_entries(
    sandbox_path: Path,
    filtered_files: list[Path],
) -> list[FileTreeEntry]:
    """Build flat MongoDB file entries from filtered files."""

    return [
        FileTreeEntry(
            path=to_relative_posix_path(file_path, sandbox_path),
            language=LANGUAGE_BY_EXTENSION.get(file_path.suffix.lower()),
            size_bytes=file_path.stat().st_size,
            line_count=count_lines(file_path),
            should_review=True,
        )
        for file_path in filtered_files
    ]


def count_lines(file_path: Path) -> int:
    """Count text lines without failing the whole analysis for one file."""

    try:
        return len(file_path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0
