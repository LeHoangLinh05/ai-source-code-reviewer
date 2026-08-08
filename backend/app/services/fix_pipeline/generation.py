"""Patch generation helpers for fix jobs."""

from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.ai.json_utils import parse_json_object_text
from app.analyzers.static_analysis.base import run_static_command
from app.models.review_issue import IssueSource, ReviewIssue
from app.schemas.fix_job import (
    FixValidationCheck,
    FixValidationCheckStatus,
    FixValidationResult,
)
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_pipeline.workspace import resolve_repo_file

logger = logging.getLogger(__name__)

MAX_AI_FIX_FILES = 5
MAX_AI_FIX_FILE_BYTES = 80_000
MAX_ISSUES_PER_FILE_PROMPT = 8
RUFF_COMMAND = "ruff"
RUFF_RULE_CODE_MAX_LENGTH = 16
TEXT_ENCODING = "utf-8"
PYTHON_SUFFIXES = {".py"}
NODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
STATIC_RULE_ID_FIELDS = ("code", "test_id", "ruleId")


class FileFixDraft(BaseModel):
    """Structured LLM contract for one fixed source file."""

    changed: bool = False
    updated_content: str = Field(default="")
    notes: str | None = None


async def generate_fix_changes(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
    timeout_seconds: int,
) -> None:
    """Apply deterministic and AI-assisted fixes for selected issues."""

    apply_ruff_fixes(
        sandbox_path=sandbox_path,
        issues=issues,
        timeout_seconds=timeout_seconds,
    )
    ai_issues = _issues_requiring_ai_fix(issues)
    if ai_issues:
        await apply_ai_file_fixes(sandbox_path=sandbox_path, issues=ai_issues)


def apply_ruff_fixes(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
    timeout_seconds: int,
) -> None:
    """Run Ruff autofix for selected Ruff rule codes and files."""

    selected_codes = _selected_ruff_codes(issues)
    selected_files = _selected_ruff_files(sandbox_path, issues)
    if not selected_codes or not selected_files:
        return

    if shutil.which(RUFF_COMMAND) is None:
        logger.info("Skipping Ruff autofix because ruff is unavailable on PATH")
        return

    command = [
        RUFF_COMMAND,
        "check",
        "--fix",
        "--select",
        ",".join(sorted(selected_codes)),
        *selected_files,
    ]
    exit_code, _stdout, stderr, _duration_ms = run_static_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
    )
    if exit_code not in {0, 1}:
        raise FixPipelineError(f"Ruff autofix failed: {stderr.strip()}")


async def apply_ai_file_fixes(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
) -> None:
    """Ask the configured LLM to rewrite affected files for selected issues."""

    issues_by_file = _group_ai_issues_by_file(sandbox_path, issues)
    resolved_root = sandbox_path.resolve()
    for index, (file_path, file_issues) in enumerate(issues_by_file.items()):
        if index >= MAX_AI_FIX_FILES:
            logger.info("Skipping AI fixes after %d files", MAX_AI_FIX_FILES)
            break

        original_content = _read_fixable_file(file_path)
        draft = await _generate_file_fix_draft(
            relative_path=file_path.relative_to(resolved_root).as_posix(),
            original_content=original_content,
            issues=file_issues[:MAX_ISSUES_PER_FILE_PROMPT],
        )
        if draft.changed and draft.updated_content != original_content:
            file_path.write_text(draft.updated_content, encoding=TEXT_ENCODING)


async def repair_validation_failures(
    *,
    sandbox_path: Path,
    changed_files: list[str],
    validation_result: FixValidationResult,
) -> bool:
    """Ask the LLM to repair changed files using concrete validation failures."""

    failed_checks = [
        check
        for check in validation_result.checks
        if check.status == FixValidationCheckStatus.FAILED
    ]
    if not failed_checks:
        return False

    changed_file_paths = _resolve_changed_text_files(sandbox_path, changed_files)
    if not changed_file_paths:
        return False

    changed = False
    resolved_root = sandbox_path.resolve()
    for index, file_path in enumerate(changed_file_paths):
        if index >= MAX_AI_FIX_FILES:
            logger.info("Skipping validation repair after %d files", MAX_AI_FIX_FILES)
            break

        original_content = _read_fixable_file(file_path)
        draft = await _generate_validation_repair_draft(
            relative_path=file_path.relative_to(resolved_root).as_posix(),
            original_content=original_content,
            validation_summary=validation_result.summary,
            failed_checks=failed_checks,
        )
        if draft.changed and draft.updated_content != original_content:
            file_path.write_text(draft.updated_content, encoding=TEXT_ENCODING)
            changed = True

    return changed


def parse_file_fix_draft_text(text: str) -> FileFixDraft:
    """Parse a file-fix draft from raw model text."""

    payload = parse_json_object_text(text)
    if payload is None:
        raise ValueError("File fix JSON object was not found")

    return FileFixDraft.model_validate(payload)


def _issues_requiring_ai_fix(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return [
        issue
        for issue in issues
        if issue.source != IssueSource.RUFF or not _has_ruff_autofix(issue)
    ]


def _has_ruff_autofix(issue: ReviewIssue) -> bool:
    fix = issue.raw_output.get("fix") if issue.raw_output is not None else None
    return isinstance(fix, dict)


def _selected_ruff_codes(issues: list[ReviewIssue]) -> set[str]:
    codes: set[str] = set()
    for issue in issues:
        if issue.source != IssueSource.RUFF:
            continue

        code = _raw_string(issue.raw_output, "code")
        if code is None or len(code) > RUFF_RULE_CODE_MAX_LENGTH:
            continue

        if code.replace("-", "").isalnum():
            codes.add(code)

    return codes


def _selected_ruff_files(sandbox_path: Path, issues: list[ReviewIssue]) -> list[str]:
    files: set[str] = set()
    for issue in issues:
        if issue.source != IssueSource.RUFF:
            continue

        file_path = resolve_repo_file(sandbox_path, issue.file_path)
        if file_path.exists() and file_path.suffix == ".py":
            files.add(file_path.relative_to(sandbox_path).as_posix())

    return sorted(files)


def _group_ai_issues_by_file(
    sandbox_path: Path,
    issues: list[ReviewIssue],
) -> dict[Path, list[ReviewIssue]]:
    grouped_issues: dict[Path, list[ReviewIssue]] = defaultdict(list)
    for issue in issues:
        file_path = resolve_repo_file(sandbox_path, issue.file_path)
        if not file_path.exists() or not file_path.is_file():
            continue

        grouped_issues[file_path].append(issue)

    return dict(grouped_issues)


def _read_fixable_file(file_path: Path) -> str:
    file_size = file_path.stat().st_size
    if file_size > MAX_AI_FIX_FILE_BYTES:
        raise FixPipelineError(f"File is too large for AI fix: {file_path}")

    try:
        return file_path.read_text(encoding=TEXT_ENCODING)
    except UnicodeDecodeError as error:
        raise FixPipelineError(f"File is not UTF-8 text: {file_path}") from error


def _resolve_changed_text_files(
    sandbox_path: Path,
    changed_files: list[str],
) -> list[Path]:
    resolved_files: list[Path] = []
    for changed_file in changed_files:
        file_path = resolve_repo_file(sandbox_path, changed_file)
        if not file_path.exists() or not file_path.is_file():
            continue

        if file_path.suffix in PYTHON_SUFFIXES | NODE_SUFFIXES:
            resolved_files.append(file_path)

    return resolved_files


async def _generate_file_fix_draft(
    *,
    relative_path: str,
    original_content: str,
    issues: list[ReviewIssue],
) -> FileFixDraft:
    from app.ai.llm.config import run_with_configured_llm

    async def call(llm: Any) -> FileFixDraft:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You generate minimal code fixes for selected review findings. "
                    "Return only valid JSON.",
                ),
                (
                    "human",
                    _build_file_fix_prompt(
                        relative_path=relative_path,
                        original_content=original_content,
                        issues=issues,
                    ),
                ),
            ]
        )
        return parse_file_fix_draft_text(_message_content(result))

    return await run_with_configured_llm(call)


async def _generate_validation_repair_draft(
    *,
    relative_path: str,
    original_content: str,
    validation_summary: str,
    failed_checks: list[FixValidationCheck],
) -> FileFixDraft:
    from app.ai.llm.config import run_with_configured_llm

    async def call(llm: Any) -> FileFixDraft:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You repair generated code patches using validation failures. "
                    "Return only valid JSON.",
                ),
                (
                    "human",
                    _build_validation_repair_prompt(
                        relative_path=relative_path,
                        original_content=original_content,
                        validation_summary=validation_summary,
                        failed_checks=failed_checks,
                    ),
                ),
            ]
        )
        return parse_file_fix_draft_text(_message_content(result))

    return await run_with_configured_llm(call)


def _build_file_fix_prompt(
    *,
    relative_path: str,
    original_content: str,
    issues: list[ReviewIssue],
) -> str:
    issue_payload = [
        {
            "id": str(issue.id),
            "line_start": issue.line_start,
            "line_end": issue.line_end,
            "severity": issue.severity.value,
            "category": issue.category.value,
            "source": issue.source.value,
            "rule_id": _issue_rule_id(issue),
            "title": issue.title,
            "description": issue.description,
            "suggestion": issue.suggestion,
        }
        for issue in issues
    ]
    return (
        "Fix only the selected issues for this file. Preserve unrelated behavior, "
        "formatting style, public APIs, and comments unless they are part of the fix. "
        "Return JSON with keys changed, updated_content, notes. "
        "Set changed=false and updated_content to the original content when no safe "
        "fix can be made.\n\n"
        f"File path: {relative_path}\n"
        f"Selected issues:\n{json.dumps(issue_payload, ensure_ascii=False)}\n\n"
        f"Current file content:\n```text\n{original_content}\n```"
    )


def _build_validation_repair_prompt(
    *,
    relative_path: str,
    original_content: str,
    validation_summary: str,
    failed_checks: list[FixValidationCheck],
) -> str:
    failed_check_payload = [
        {
            "name": check.name,
            "command": check.command,
            "exit_code": check.exit_code,
            "stdout": check.stdout,
            "stderr": check.stderr,
        }
        for check in failed_checks
    ]
    return (
        "A generated patch failed validation. Repair only this file if it is "
        "needed to make the validation pass. Keep the original fix intent, "
        "preserve unrelated behavior, and avoid broad refactors. Return JSON "
        "with keys changed, updated_content, notes. Set changed=false and "
        "updated_content to the original content when this file does not need "
        "a repair.\n\n"
        f"File path: {relative_path}\n"
        f"Validation summary: {validation_summary}\n"
        f"Failed checks:\n{json.dumps(failed_check_payload, ensure_ascii=False)}\n\n"
        f"Current file content:\n```text\n{original_content}\n```"
    )


def _issue_rule_id(issue: ReviewIssue) -> str | None:
    for field in STATIC_RULE_ID_FIELDS:
        rule_id = _raw_string(issue.raw_output, field)
        if rule_id is not None:
            return rule_id

    return None


def _message_content(result: object) -> str:
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_message_content_item_text(item) for item in content)

    return str(result)


def _message_content_item_text(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        if isinstance(text, str):
            return text
        content = item.get("content")
        if isinstance(content, str):
            return content

    return str(item)


def _raw_string(raw_output: dict[str, object] | None, key: str) -> str | None:
    if raw_output is None:
        return None

    value = raw_output.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None
