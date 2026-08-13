"""Generate coherent multi-file patches for selected issues."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.ai.json_utils import parse_json_object_text
from app.models.review_issue import IssueSource, ReviewIssue
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixValidationResult,
)
from app.services.fix_jobs.pipeline.contracts import (
    READ_ONLY_FIX_CONTEXT_FILE_NAMES,
    FixGenerationDisposition,
    FixGenerationDispositionStatus,
    FixGenerationResponse,
    FixIssueSpec,
)
from app.services.fix_jobs.pipeline.dependencies import run_safe_command
from app.services.fix_jobs.pipeline.errors import FixPipelineError
from app.services.fix_jobs.pipeline.execution import FixCommandExecutor
from app.services.fix_jobs.pipeline.workspace import resolve_repo_file

MAX_AI_FIX_FILE_BYTES = 80_000
MAX_GENERATION_CONTRACT_RETRIES = 1
RUFF_COMMAND = "ruff"
RUFF_RULE_CODE_MAX_LENGTH = 16
TEXT_ENCODING = "utf-8"

logger = logging.getLogger(__name__)


class _GenerationResponseContractError(ValueError):
    """Raised when generation output cannot satisfy the typed response contract."""


async def generate_fix_changes(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> list[FixGenerationDisposition]:
    """Apply deterministic fixes and coherent LLM patches for every planned issue."""

    _validate_plan_paths(specs=specs, plans=plans)
    deterministic_fix_available = apply_ruff_fixes(
        sandbox_path=sandbox_path,
        issues=issues,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    ai_issue_ids = {
        issue.id
        for issue in issues
        if _issue_requires_ai_fix(issue) or not deterministic_fix_available
    }
    ai_plans = [
        plan
        for plan in plans
        if plan.status == FixIssuePlanStatus.PLANNED and plan.issue_id in ai_issue_ids
    ]
    dispositions = await _generate_plan_components(
        sandbox_path=sandbox_path,
        specs=specs,
        plans=ai_plans,
        repair_context=None,
    )
    dispositions_by_id = {
        disposition.issue_id: disposition for disposition in dispositions
    }
    for plan in plans:
        if plan.issue_id in dispositions_by_id:
            continue
        if plan.status != FixIssuePlanStatus.PLANNED:
            status = FixGenerationDispositionStatus.UNCERTAIN
            summary = plan.reason or "The issue planner could not produce a safe plan."
        else:
            status = FixGenerationDispositionStatus.CHANGED
            summary = "A deterministic static-analyzer fix was applied."
        dispositions_by_id[plan.issue_id] = FixGenerationDisposition(
            issue_id=plan.issue_id,
            status=status,
            summary=summary,
        )
    return [dispositions_by_id[plan.issue_id] for plan in plans]


async def repair_fix_failures(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    issue_results: list[FixIssueResult],
    validation_result: FixValidationResult | None = None,
) -> bool:
    """Repair unresolved logic contracts and optional command failures."""

    failed_ids = {
        result.issue_id for result in issue_results if result.verdict.value != "fixed"
    }
    failed_plans = [
        plan
        for plan in plans
        if plan.status == FixIssuePlanStatus.PLANNED
        and (
            plan.issue_id in failed_ids
            or _overlaps_failed_plan(plan, plans, failed_ids)
        )
    ]
    has_command_failure = any(
        check.status.value == "failed" and check.kind.value != "semantic"
        for check in (validation_result.checks if validation_result else [])
    )
    if has_command_failure and not failed_plans:
        failed_plans = [
            plan for plan in plans if plan.status == FixIssuePlanStatus.PLANNED
        ]
    if not failed_plans and not has_command_failure:
        return False

    repair_context: dict[str, object] = {
        "issue_results": [result.model_dump(mode="json") for result in issue_results],
        "failed_checks": [
            check.model_dump(mode="json")
            for check in (validation_result.checks if validation_result else [])
            if check.status.value == "failed"
        ],
    }
    before = {
        path: _read_fixable_file(resolve_repo_file(sandbox_path, path))
        for path in _editable_paths(failed_plans)
    }
    await _generate_plan_components(
        sandbox_path=sandbox_path,
        specs=specs,
        plans=failed_plans,
        repair_context=repair_context,
    )
    return any(
        _read_fixable_file(resolve_repo_file(sandbox_path, path)) != content
        for path, content in before.items()
    )


def apply_ruff_fixes(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> bool:
    """Run Ruff autofix for selected Ruff rule codes and files."""

    selected_codes = _selected_ruff_codes(issues)
    selected_files = _selected_ruff_files(sandbox_path, issues)
    if not selected_codes or not selected_files:
        return True
    command = [
        RUFF_COMMAND,
        "check",
        "--fix",
        "--select",
        ",".join(sorted(selected_codes)),
        *selected_files,
    ]
    exit_code, _stdout, stderr, _duration_ms = run_safe_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    if exit_code not in {0, 1}:
        logger.warning("Ruff autofix unavailable; using AI generation: %s", stderr)
        return False
    return True


async def _generate_plan_components(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    repair_context: dict[str, object] | None,
) -> list[FixGenerationDisposition]:
    specs_by_id = {spec.issue_id: spec for spec in specs}
    dispositions: list[FixGenerationDisposition] = []
    for component in _connected_plan_components(plans):
        component_specs = [specs_by_id[plan.issue_id] for plan in component]
        response = await _request_generation_with_contract(
            sandbox_path=sandbox_path,
            specs=component_specs,
            plans=component,
            repair_context=repair_context,
        )
        _apply_generation_response(
            sandbox_path=sandbox_path,
            plans=component,
            response=response,
        )
        dispositions.extend(response.dispositions)
    return dispositions


async def _request_generation_with_contract(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    repair_context: dict[str, object] | None,
) -> FixGenerationResponse:
    requested_ids = [plan.issue_id for plan in plans]
    retry_reason: str | None = None
    for attempt in range(MAX_GENERATION_CONTRACT_RETRIES + 1):
        try:
            response = await _request_generation(
                sandbox_path=sandbox_path,
                specs=specs,
                plans=plans,
                repair_context=repair_context,
                retry_reason=retry_reason,
            )
        except _GenerationResponseContractError as error:
            retry_reason = str(error)
            _log_generation_contract_retry(
                attempt=attempt,
                requested_ids=requested_ids,
                reason=retry_reason,
            )
            continue

        retry_reason = _disposition_contract_error(response, requested_ids)
        if retry_reason is None:
            return response
        _log_generation_contract_retry(
            attempt=attempt,
            requested_ids=requested_ids,
            reason=retry_reason,
        )

    raise FixPipelineError(
        "Fix generation response contract failed after "
        f"{MAX_GENERATION_CONTRACT_RETRIES + 1} attempts for issues "
        + ", ".join(str(issue_id) for issue_id in requested_ids)
        + f". Last error: {retry_reason or 'unknown contract error'}"
    )


async def _request_generation(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    repair_context: dict[str, object] | None,
    retry_reason: str | None,
) -> FixGenerationResponse:
    from app.ai.llm.config import run_with_configured_llm

    async def call(llm: Any) -> FixGenerationResponse:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You implement minimal, coherent multi-file fixes from explicit "
                    "safety contracts. Return only valid JSON.",
                ),
                (
                    "human",
                    _build_generation_prompt(
                        sandbox_path=sandbox_path,
                        specs=specs,
                        plans=plans,
                        repair_context=repair_context,
                        retry_reason=retry_reason,
                    ),
                ),
            ]
        )
        payload = parse_json_object_text(_message_content(result))
        if payload is None:
            raise _GenerationResponseContractError(
                "response did not contain a valid JSON object"
            )
        try:
            return FixGenerationResponse.model_validate(payload)
        except ValidationError as error:
            raise _GenerationResponseContractError(
                _summarize_generation_validation_error(error)
            ) from error

    return await run_with_configured_llm(call)


def _build_generation_prompt(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    repair_context: dict[str, object] | None,
    retry_reason: str | None,
) -> str:
    context_paths = list(
        dict.fromkeys(
            path
            for plan in plans
            for path in [*plan.context_files, *plan.editable_files]
        )
    )
    source_files = {
        path: _read_fixable_file(resolve_repo_file(sandbox_path, path))
        for path in context_paths
    }
    payload = {
        "issues": [
            spec.model_dump(mode="json", exclude={"source_files"}) for spec in specs
        ],
        "plans": [plan.model_dump(mode="json") for plan in plans],
        "current_source_files": source_files,
        "repair_context": repair_context,
    }
    retry_text = (
        f"Previous response contract error: {retry_reason}. Correct it.\n\n"
        if retry_reason
        else ""
    )
    return (
        f"{retry_text}Return exactly one JSON object using this shape:\n"
        '{"files":[{"path":"<planned relative path>",'
        '"updated_content":"<complete replacement content>"}],'
        '"dispositions":[{"issue_id":"<requested UUID>",'
        '"status":"changed","summary":"<what changed for this issue>"}]}\n'
        "Both files and dispositions must be JSON arrays. Never encode files as an "
        "object keyed by path. Every disposition must include a non-empty summary. "
        "files contains complete replacement "
        "content only for changed editable_files. dispositions must contain exactly "
        "one item for each requested issue_id, with status changed, not_changed, or "
        "uncertain. Do not return unknown issue IDs, duplicate paths, test files, or "
        "files outside editable_files. Satisfy every acceptance_check and avoid every "
        "forbidden_shortcut. Preserve unrelated APIs and behavior. Treat the patch as "
        "one cross-file change: update every necessary caller, schema, service, "
        "repository, dependency manifest, and configuration template. Verify that "
        "each import matches both the declared package and that package's actual API; "
        "package and import names may differ (python-jose uses "
        "`from jose import jwt`). "
        "Do not use a default value as a substitute for enforcing lower and upper "
        "bounds on user-controlled numeric input. Never add predictable fallback "
        "secrets. Do not alter unrelated suppressions or findings. For repair work, "
        "use the concrete failed checks as evidence and keep the original fix "
        "intent.\n\n" + json.dumps(payload, ensure_ascii=False)
    )


def _apply_generation_response(
    *,
    sandbox_path: Path,
    plans: list[FixIssuePlan],
    response: FixGenerationResponse,
) -> None:
    allowed_paths = set(_editable_paths(plans))
    returned_paths = [updated_file.path for updated_file in response.files]
    if len(returned_paths) != len(set(returned_paths)):
        raise FixPipelineError("Fix generation returned duplicate file paths")
    for updated_file in response.files:
        if updated_file.path not in allowed_paths:
            raise FixPipelineError(
                f"Fix generation attempted an unplanned file: {updated_file.path}"
            )
        if _is_test_file(updated_file.path):
            raise FixPipelineError(
                "Fix generation attempted to create or edit a test: "
                f"{updated_file.path}"
            )
        encoded = updated_file.updated_content.encode(TEXT_ENCODING)
        if len(encoded) > MAX_AI_FIX_FILE_BYTES:
            raise FixPipelineError(f"Generated file is too large: {updated_file.path}")
        file_path = resolve_repo_file(sandbox_path, updated_file.path)
        if not file_path.is_file():
            raise FixPipelineError(
                f"Fix generation cannot create an untracked file: {updated_file.path}"
            )
        original_content = _read_fixable_file(file_path)
        if original_content != updated_file.updated_content:
            file_path.write_text(updated_file.updated_content, encoding=TEXT_ENCODING)


def _validate_plan_paths(
    *, specs: list[FixIssueSpec], plans: list[FixIssuePlan]
) -> None:
    specs_by_id = {spec.issue_id: spec for spec in specs}
    selected_context_paths = {path for spec in specs for path in spec.source_files}
    for plan in plans:
        spec = specs_by_id.get(plan.issue_id)
        if spec is None:
            raise FixPipelineError(f"Planner returned unknown issue {plan.issue_id}")
        returned_paths = set(plan.editable_files) | set(plan.context_files)
        returned_paths.update(
            related_file
            for scenario in [
                *plan.exploit_scenarios,
                *plan.preserved_behavior_scenarios,
            ]
            for related_file in scenario.related_files
        )
        unknown_paths = returned_paths - selected_context_paths
        if unknown_paths:
            raise FixPipelineError(
                "Fix planner selected files outside selected issue context: "
                + ", ".join(sorted(unknown_paths))
            )
        if plan.status == FixIssuePlanStatus.PLANNED and not plan.editable_files:
            raise FixPipelineError(
                f"Fix planner returned no editable files for issue {plan.issue_id}"
            )
        read_only_paths = {
            path
            for path in plan.editable_files
            if Path(path).name in READ_ONLY_FIX_CONTEXT_FILE_NAMES
        }
        if read_only_paths:
            raise FixPipelineError(
                "Fix planner selected read-only instruction files: "
                + ", ".join(sorted(read_only_paths))
            )


def _connected_plan_components(
    plans: list[FixIssuePlan],
) -> list[list[FixIssuePlan]]:
    remaining = list(plans)
    components: list[list[FixIssuePlan]] = []
    while remaining:
        component = [remaining.pop(0)]
        component_paths = _plan_paths(component[0])
        index = 0
        while index < len(remaining):
            candidate = remaining[index]
            if component_paths & _plan_paths(candidate):
                component.append(remaining.pop(index))
                component_paths.update(_plan_paths(candidate))
                index = 0
                continue
            index += 1
        components.append(component)
    return components


def _overlaps_failed_plan(
    plan: FixIssuePlan,
    plans: list[FixIssuePlan],
    failed_ids: set[UUID],
) -> bool:
    return any(
        other.issue_id in failed_ids and _plan_paths(plan) & _plan_paths(other)
        for other in plans
    )


def _plan_paths(plan: FixIssuePlan) -> set[str]:
    return set(plan.editable_files) | set(plan.context_files)


def _editable_paths(plans: Iterable[FixIssuePlan]) -> list[str]:
    return list(dict.fromkeys(path for plan in plans for path in plan.editable_files))


def _has_exact_dispositions(
    response: FixGenerationResponse,
    requested_ids: list[UUID],
) -> bool:
    received_ids = [item.issue_id for item in response.dispositions]
    return len(received_ids) == len(requested_ids) and set(received_ids) == set(
        requested_ids
    )


def _disposition_contract_error(
    response: FixGenerationResponse,
    requested_ids: list[UUID],
) -> str | None:
    if _has_exact_dispositions(response, requested_ids):
        return None

    received_ids = [item.issue_id for item in response.dispositions]
    missing_ids = sorted(set(requested_ids) - set(received_ids), key=str)
    unknown_ids = sorted(set(received_ids) - set(requested_ids), key=str)
    duplicate_ids = sorted(
        {issue_id for issue_id in received_ids if received_ids.count(issue_id) > 1},
        key=str,
    )
    return (
        "dispositions must contain exactly one item per requested issue; "
        f"missing={_format_issue_ids(missing_ids)}, "
        f"duplicate={_format_issue_ids(duplicate_ids)}, "
        f"unknown={_format_issue_ids(unknown_ids)}"
    )


def _summarize_generation_validation_error(error: ValidationError) -> str:
    details = [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors(include_url=False, include_input=False)
    ]
    return "response schema validation failed: " + json.dumps(details)


def _log_generation_contract_retry(
    *,
    attempt: int,
    requested_ids: list[UUID],
    reason: str,
) -> None:
    if attempt >= MAX_GENERATION_CONTRACT_RETRIES:
        return
    logger.warning(
        "Fix generation contract attempt %d/%d failed for issues %s; retrying: %s",
        attempt + 1,
        MAX_GENERATION_CONTRACT_RETRIES + 1,
        ", ".join(str(issue_id) for issue_id in requested_ids),
        reason,
    )


def _format_issue_ids(issue_ids: list[UUID]) -> str:
    return "[" + ", ".join(str(issue_id) for issue_id in issue_ids) + "]"


def _issue_requires_ai_fix(issue: ReviewIssue) -> bool:
    return issue.source != IssueSource.RUFF or not _has_ruff_autofix(issue)


def _has_ruff_autofix(issue: ReviewIssue) -> bool:
    fix = (issue.raw_output or {}).get("fix")
    return isinstance(fix, dict)


def _selected_ruff_codes(issues: list[ReviewIssue]) -> set[str]:
    codes: set[str] = set()
    for issue in issues:
        if issue.source != IssueSource.RUFF:
            continue
        code = _raw_string(issue.raw_output, "code")
        if (
            code is not None
            and len(code) <= RUFF_RULE_CODE_MAX_LENGTH
            and code.replace("-", "").isalnum()
        ):
            codes.add(code)
    return codes


def _selected_ruff_files(sandbox_path: Path, issues: list[ReviewIssue]) -> list[str]:
    files: set[str] = set()
    for issue in issues:
        if issue.source != IssueSource.RUFF:
            continue
        if _is_test_file(issue.file_path):
            continue
        file_path = resolve_repo_file(sandbox_path, issue.file_path)
        if file_path.is_file() and file_path.suffix == ".py":
            files.add(file_path.relative_to(sandbox_path).as_posix())
    return sorted(files)


def _read_fixable_file(file_path: Path) -> str:
    if file_path.stat().st_size > MAX_AI_FIX_FILE_BYTES:
        raise FixPipelineError(f"File is too large for AI fix: {file_path}")
    try:
        return file_path.read_text(encoding=TEXT_ENCODING)
    except UnicodeDecodeError as error:
        raise FixPipelineError(f"File is not UTF-8 text: {file_path}") from error


def _raw_string(raw_output: dict[str, object] | None, key: str) -> str | None:
    value = (raw_output or {}).get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_test_file(path: str) -> bool:
    normalized_path = path.replace("\\", "/").casefold()
    normalized = f"/{normalized_path}"
    name = Path(path).name.casefold()
    return (
        "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def _message_content(result: object) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(result)
