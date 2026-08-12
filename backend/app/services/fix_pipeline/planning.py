"""Cross-file context collection and strict issue planning."""

from __future__ import annotations

import ast
import json
import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.ai.json_utils import parse_json_object_text
from app.ai.probe.plan import BASELINE_PROBES
from app.models.review_issue import ReviewIssue
from app.schemas.fix_job import FixIssuePlan, FixIssuePlanStatus, FixScenarioKind
from app.services.fix_pipeline.contracts import (
    READ_ONLY_FIX_CONTEXT_FILE_NAMES,
    FixIssueSpec,
    FixPlanningResponse,
    get_probe_id,
    get_probe_review,
)
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_pipeline.workspace import resolve_repo_file

MAX_CONTEXT_FILES_PER_ISSUE = 20
MAX_CONTEXT_FILE_BYTES = 80_000
MAX_CONTRACT_RETRIES = 1
MIN_RELATED_SYMBOL_LENGTH = 4
TEXT_ENCODING = "utf-8"
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
PROJECT_SUPPORT_FILE_NAMES = {
    ".env.example",
    "AGENTS.md",
    "Dockerfile",
    "Pipfile",
    "Pipfile.lock",
    "docker-compose.yaml",
    "docker-compose.yml",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tsconfig.json",
    "uv.lock",
    "yarn.lock",
}
REQUIREMENTS_FILE_PATTERN = re.compile(r"requirements(?:-[\w.-]+)?\.txt$")
IGNORED_CONTEXT_PARTS = {
    ".git",
    ".repoguard-env",
    ".repoguard-tests",
    ".venv",
    "node_modules",
}
TEST_FILE_MARKERS = ("test_", "_test.", ".test.", ".spec.")
STATIC_RULE_ID_FIELDS = ("code", "test_id", "ruleId")
PROBE_QUESTIONS = {probe.probe_id: probe.judge_question for probe in BASELINE_PROBES}

logger = logging.getLogger(__name__)


class _PlanningResponseContractError(ValueError):
    """Raised when planner output cannot satisfy the typed response contract."""


async def build_fix_issue_plans(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
) -> tuple[list[FixIssueSpec], list[FixIssuePlan]]:
    """Build bounded issue specs and require exactly one plan for each issue."""

    specs = [
        build_fix_issue_spec(sandbox_path=sandbox_path, issue=issue) for issue in issues
    ]
    plans = await _request_plans_with_contract(specs)
    _include_project_support_context(specs=specs, plans=plans)
    return specs, plans


def build_fix_issue_spec(*, sandbox_path: Path, issue: ReviewIssue) -> FixIssueSpec:
    """Normalize one issue and collect its cross-file source context."""

    probe_review = get_probe_review(issue)
    probe_id = get_probe_id(issue)
    supporting_evidence = _dict_items(probe_review.get("supporting_evidence"))
    context_paths, context_truncated = _collect_context_paths(
        sandbox_path=sandbox_path,
        issue=issue,
        supporting_evidence=supporting_evidence,
    )
    source_files = {
        path: _read_context_file(resolve_repo_file(sandbox_path, path))
        for path in context_paths
    }
    return FixIssueSpec(
        issue_id=issue.id,
        probe_id=probe_id,
        judge_question=PROBE_QUESTIONS.get(probe_id or ""),
        file_path=issue.file_path,
        line_start=issue.line_start,
        line_end=issue.line_end,
        severity=issue.severity.value,
        category=issue.category.value,
        source=issue.source.value,
        rule_id=_issue_rule_id(issue),
        title=issue.title,
        description=issue.description,
        suggestion=issue.suggestion,
        supporting_evidence=supporting_evidence,
        source_files=source_files,
        context_truncated=context_truncated,
    )


async def _request_plans_with_contract(
    specs: list[FixIssueSpec],
) -> list[FixIssuePlan]:
    requested_ids = [spec.issue_id for spec in specs]
    specs_by_id = {spec.issue_id: spec for spec in specs}
    retry_reason: str | None = None
    for attempt in range(MAX_CONTRACT_RETRIES + 1):
        try:
            response = await _request_plans(specs, retry_reason=retry_reason)
        except _PlanningResponseContractError as error:
            retry_reason = str(error)
            _log_planning_contract_retry(
                attempt=attempt,
                requested_ids=requested_ids,
                reason=retry_reason,
            )
            continue

        retry_reason = _planning_contract_error(
            response=response,
            requested_ids=requested_ids,
            specs_by_id=specs_by_id,
        )
        if retry_reason is None:
            return _ordered_by_issue_id(response.plans, requested_ids)
        _log_planning_contract_retry(
            attempt=attempt,
            requested_ids=requested_ids,
            reason=retry_reason,
        )

    plans: list[FixIssuePlan] = []
    for spec in specs:
        plans.append(await _request_single_plan_with_contract(spec))
    return plans


async def _request_single_plan_with_contract(spec: FixIssueSpec) -> FixIssuePlan:
    retry_reason: str | None = (
        "The grouped response violated the one-plan-per-issue contract."
    )
    for attempt in range(MAX_CONTRACT_RETRIES + 1):
        try:
            response = await _request_plans([spec], retry_reason=retry_reason)
        except _PlanningResponseContractError as error:
            retry_reason = str(error)
            _log_planning_contract_retry(
                attempt=attempt,
                requested_ids=[spec.issue_id],
                reason=retry_reason,
            )
            continue

        retry_reason = _planning_contract_error(
            response=response,
            requested_ids=[spec.issue_id],
            specs_by_id={spec.issue_id: spec},
        )
        if retry_reason is None:
            return response.plans[0]
        _log_planning_contract_retry(
            attempt=attempt,
            requested_ids=[spec.issue_id],
            reason=retry_reason,
        )

    raise FixPipelineError(
        f"Fix planner contract failed for issue {spec.issue_id} after "
        f"{MAX_CONTRACT_RETRIES + 1} attempts. Last error: "
        f"{retry_reason or 'unknown contract error'}"
    )


async def _request_plans(
    specs: list[FixIssueSpec],
    *,
    retry_reason: str | None,
) -> FixPlanningResponse:
    from app.ai.llm.config import run_with_configured_llm

    async def call(llm: Any) -> FixPlanningResponse:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You plan minimal, correct multi-file fixes. Treat finding prose "
                    "as a hypothesis and re-check source evidence. Return only JSON.",
                ),
                ("human", _build_planning_prompt(specs, retry_reason=retry_reason)),
            ]
        )
        payload = parse_json_object_text(_message_content(result))
        if payload is None:
            raise _PlanningResponseContractError(
                "response did not contain a valid JSON object"
            )
        try:
            return FixPlanningResponse.model_validate(payload)
        except ValidationError as error:
            raise _PlanningResponseContractError(
                _summarize_planning_validation_error(error)
            ) from error

    return await run_with_configured_llm(call)


def _build_planning_prompt(
    specs: list[FixIssueSpec],
    *,
    retry_reason: str | None,
) -> str:
    payload = [spec.model_dump(mode="json") for spec in specs]
    retry_text = f"\nPrevious response error: {retry_reason}\n" if retry_reason else ""
    return (
        "Return an object with a plans array containing exactly one plan for every "
        "requested issue_id and no unknown IDs. Each plan must contain issue_id, "
        "probe_id, root_cause, safety_property, editable_files, context_files, "
        "affected_contracts, exploit_scenarios, preserved_behavior_scenarios, "
        "acceptance_checks, forbidden_shortcuts, status, and reason. "
        "affected_contracts must be a JSON array of non-empty strings, never "
        'objects; for example ["POST /api/items request body"]. Each scenario '
        "must contain scenario_id, kind, description, and related_files. A planned "
        "issue must include at least one exploit scenario and one preserved_behavior "
        "scenario, plus at least one affected contract. Preserved behavior must cover "
        "legitimate success paths and existing error behavior for every affected "
        "contract. Include backend API and frontend consumer behavior when a schema "
        "or response shape crosses that boundary. status must be "
        "planned, not_fixable, or uncertain. editable_files must be a subset of "
        "source_files supplied across all selected issues. Include every file needed "
        "for a coherent route/schema/service/repository fix, including dependency "
        "manifests and configuration templates when imports or configuration change. "
        "Before planning, trace callers, imported APIs, request/response schemas, "
        "frontend consumers, dependency declarations, and configuration contracts. "
        "An import must match the API exposed by the declared dependency; package and "
        "import names may differ (for example python-jose uses `from jose import jwt`, "
        "not `import jwt`). Prefer an already declared dependency. If a new dependency "
        "requires a lockfile update that cannot be produced coherently, mark the plan "
        "uncertain. Defaults do not bound user input: pagination and other numeric "
        "inputs need explicit lower and upper constraints. Never introduce a "
        "predictable fallback for secrets or credentials. Only include edits causally "
        "required by the selected issue; do not remove unrelated suppressions. Treat "
        "AGENTS.md as read-only implementation instructions, never an editable file. "
        "Pydantic rejects unknown "
        "fields only; a sensitive field declared in a request schema is still "
        "mass assignment. Do not propose response_model_exclude as proof that a "
        "sensitive field is absent from list responses. Do not create tests in the "
        "target repository."
        " If context_truncated is true, status must be uncertain because required "
        "cross-file context may be missing."
        f"{retry_text}\nIssues:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _collect_context_paths(
    *,
    sandbox_path: Path,
    issue: ReviewIssue,
    supporting_evidence: list[dict[str, object]],
) -> tuple[list[str], bool]:
    primary_paths = [issue.file_path]
    primary_paths.extend(
        value
        for item in supporting_evidence
        if isinstance((value := item.get("file_path")), str)
    )
    requested_paths = list(dict.fromkeys(primary_paths))
    existing_paths = _unique_existing_source_paths(sandbox_path, requested_paths)
    support_paths = _find_project_support_paths(sandbox_path, existing_paths)
    related_paths = _find_related_source_paths(sandbox_path, existing_paths)
    combined = list(dict.fromkeys([*existing_paths, *support_paths, *related_paths]))
    context_incomplete = len(existing_paths) < len(requested_paths)
    return (
        combined[:MAX_CONTEXT_FILES_PER_ISSUE],
        context_incomplete or len(combined) > MAX_CONTEXT_FILES_PER_ISSUE,
    )


def _unique_existing_source_paths(
    sandbox_path: Path,
    paths: Iterable[str],
) -> list[str]:
    result: list[str] = []
    for path in paths:
        try:
            resolved = resolve_repo_file(sandbox_path, path)
        except FixPipelineError:
            continue
        if (
            not resolved.is_file()
            or not _is_context_file(resolved)
            or resolved.stat().st_size > MAX_CONTEXT_FILE_BYTES
        ):
            continue
        relative_path = resolved.relative_to(sandbox_path.resolve()).as_posix()
        if relative_path not in result:
            result.append(relative_path)
    return result


def _find_project_support_paths(
    sandbox_path: Path,
    seed_paths: list[str],
) -> list[str]:
    """Return bounded manifests/config templates from each seed's ancestor projects."""

    sandbox_root = sandbox_path.resolve()
    result: list[str] = []
    for seed_path in seed_paths:
        current = resolve_repo_file(sandbox_path, seed_path).parent
        while current.is_relative_to(sandbox_root):
            for candidate in sorted(current.iterdir(), key=lambda path: path.name):
                if not _is_project_support_file(candidate):
                    continue
                relative_path = candidate.relative_to(sandbox_root).as_posix()
                if relative_path not in result:
                    result.append(relative_path)
            if current == sandbox_root:
                break
            current = current.parent
    return result


def _is_context_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in SOURCE_SUFFIXES or _is_project_support_file(
        file_path
    )


def _is_project_support_file(file_path: Path) -> bool:
    return bool(
        file_path.is_file()
        and file_path.stat().st_size <= MAX_CONTEXT_FILE_BYTES
        and _is_project_support_path(file_path)
    )


def _is_project_support_path(file_path: Path) -> bool:
    return bool(
        file_path.name in PROJECT_SUPPORT_FILE_NAMES
        or REQUIREMENTS_FILE_PATTERN.fullmatch(file_path.name)
    )


def _include_project_support_context(
    *,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
) -> None:
    """Ensure generation and verification always see relevant project contracts."""

    specs_by_id = {spec.issue_id: spec for spec in specs}
    for plan in plans:
        spec = specs_by_id[plan.issue_id]
        support_paths = [
            path for path in spec.source_files if _is_project_support_path(Path(path))
        ]
        plan.context_files = list(dict.fromkeys([*plan.context_files, *support_paths]))


def _find_related_source_paths(
    sandbox_path: Path,
    seed_paths: list[str],
) -> list[str]:
    source_files = [
        path
        for path in sandbox_path.rglob("*")
        if not any(part in IGNORED_CONTEXT_PARTS for part in path.parts)
        and path.is_file()
        and path.suffix.lower() in SOURCE_SUFFIXES
    ]
    seed_modules = {_module_name(path) for path in seed_paths}
    seed_modules.update(Path(path).stem for path in seed_paths)
    seed_symbols: set[str] = set()
    for path in seed_paths:
        seed_symbols.update(_top_level_symbols(resolve_repo_file(sandbox_path, path)))

    scored: list[tuple[int, str]] = []
    for file_path in source_files:
        relative_path = file_path.relative_to(sandbox_path).as_posix()
        if (
            relative_path in seed_paths
            or file_path.stat().st_size > MAX_CONTEXT_FILE_BYTES
        ):
            continue
        content = _read_context_file(file_path)
        score = _related_score(content, seed_modules, seed_symbols)
        if score and _is_test_path(relative_path):
            score += 2
        if score:
            scored.append((score, relative_path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    first_hop = [path for _score, path in scored]
    expanded_modules = set(seed_modules)
    expanded_symbols = set(seed_symbols)
    for path in first_hop:
        expanded_modules.add(_module_name(path))
        expanded_modules.add(Path(path).stem)
        expanded_symbols.update(
            _top_level_symbols(resolve_repo_file(sandbox_path, path))
        )

    second_hop: list[tuple[int, str]] = []
    seen_paths = set(seed_paths) | set(first_hop)
    for file_path in source_files:
        relative_path = file_path.relative_to(sandbox_path).as_posix()
        if (
            relative_path in seen_paths
            or file_path.stat().st_size > MAX_CONTEXT_FILE_BYTES
        ):
            continue
        score = _related_score(
            _read_context_file(file_path),
            expanded_modules,
            expanded_symbols,
        )
        if score:
            if _is_test_path(relative_path):
                score += 2
            second_hop.append((score, relative_path))
    second_hop.sort(key=lambda item: (-item[0], item[1]))
    return [*first_hop, *(path for _score, path in second_hop)]


def _related_score(
    content: str,
    modules: set[str],
    symbols: set[str],
) -> int:
    score = sum(module in content for module in modules if module)
    score += sum(
        symbol in content
        for symbol in symbols
        if len(symbol) >= MIN_RELATED_SYMBOL_LENGTH
    )
    return score


def _module_name(path: str) -> str:
    module_path = Path(path).with_suffix("").as_posix().replace("/", ".")
    return module_path.removesuffix(".__init__")


def _top_level_symbols(file_path: Path) -> set[str]:
    if file_path.suffix.lower() != ".py":
        content = _read_context_file(file_path)
        return set(
            re.findall(
                r"(?:class|function|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)",
                content,
            )
        )
    try:
        tree = ast.parse(_read_context_file(file_path))
    except (SyntaxError, FixPipelineError):
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _is_test_path(path: str) -> bool:
    normalized = path.casefold()
    name = Path(normalized).name
    return (
        "tests/" in normalized
        or "__tests__/" in normalized
        or any(marker in name for marker in TEST_FILE_MARKERS)
    )


def _plan_satisfies_contract(
    plan: FixIssuePlan,
    *,
    context_truncated: bool = False,
) -> bool:
    if plan.status != FixIssuePlanStatus.PLANNED:
        return True
    if context_truncated:
        return False
    scenarios = [*plan.exploit_scenarios, *plan.preserved_behavior_scenarios]
    scenario_ids = [scenario.scenario_id for scenario in scenarios]
    return bool(
        plan.affected_contracts
        and plan.exploit_scenarios
        and plan.preserved_behavior_scenarios
        and len(scenario_ids) == len(set(scenario_ids))
        and not any(
            Path(path).name in READ_ONLY_FIX_CONTEXT_FILE_NAMES
            for path in plan.editable_files
        )
        and all(
            scenario.kind == FixScenarioKind.EXPLOIT
            for scenario in plan.exploit_scenarios
        )
        and all(
            scenario.kind == FixScenarioKind.PRESERVED_BEHAVIOR
            for scenario in plan.preserved_behavior_scenarios
        )
    )


def _read_context_file(file_path: Path) -> str:
    if file_path.stat().st_size > MAX_CONTEXT_FILE_BYTES:
        raise FixPipelineError(f"File is too large for fix context: {file_path}")
    try:
        return file_path.read_text(encoding=TEXT_ENCODING)
    except UnicodeDecodeError as error:
        raise FixPipelineError(f"File is not UTF-8 text: {file_path}") from error


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _issue_rule_id(issue: ReviewIssue) -> str | None:
    raw_output = issue.raw_output or {}
    for field in STATIC_RULE_ID_FIELDS:
        value = raw_output.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _has_exact_ids(items: Sequence[object], requested_ids: list[UUID]) -> bool:
    received_ids = [getattr(item, "issue_id", None) for item in items]
    return len(received_ids) == len(requested_ids) and set(received_ids) == set(
        requested_ids
    )


def _ordered_by_issue_id(
    plans: list[FixIssuePlan], requested_ids: list[UUID]
) -> list[FixIssuePlan]:
    plans_by_id = {plan.issue_id: plan for plan in plans}
    return [plans_by_id[issue_id] for issue_id in requested_ids]


def _planning_contract_error(
    *,
    response: FixPlanningResponse,
    requested_ids: list[UUID],
    specs_by_id: dict[UUID, FixIssueSpec],
) -> str | None:
    if not _has_exact_ids(response.plans, requested_ids):
        return "plans must contain exactly one plan per requested issue_id"
    if not all(
        _plan_satisfies_contract(
            plan,
            context_truncated=specs_by_id[plan.issue_id].context_truncated,
        )
        for plan in response.plans
    ):
        return "every plan must satisfy the required contract and scenario rules"
    return None


def _summarize_planning_validation_error(error: ValidationError) -> str:
    details = [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors(include_url=False, include_input=False)
    ]
    return "response schema validation failed: " + json.dumps(details)


def _log_planning_contract_retry(
    *,
    attempt: int,
    requested_ids: list[UUID],
    reason: str,
) -> None:
    if attempt >= MAX_CONTRACT_RETRIES:
        return
    logger.warning(
        "Fix planning contract attempt %d/%d failed for issues %s; retrying: %s",
        attempt + 1,
        MAX_CONTRACT_RETRIES + 1,
        ", ".join(str(issue_id) for issue_id in requested_ids),
        reason,
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
