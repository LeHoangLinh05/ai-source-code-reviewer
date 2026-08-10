"""Issue-focused semantic verification for generated patches."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.json_utils import parse_json_object_text
from app.models.fix_job import FixValidationStatus
from app.models.review_issue import IssueSource, ReviewIssue
from app.schemas.fix_job import (
    FixEvidenceReference,
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixScenarioResult,
    FixScenarioStatus,
    FixValidationCheck,
    FixValidationCheckKind,
    FixValidationCheckStatus,
    FixValidationResult,
    build_fix_validation_summary,
)
from app.services.fix_pipeline.contracts import FixIssueSpec, FixVerificationResponse
from app.services.fix_pipeline.dependencies import run_safe_command
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_pipeline.execution import FixCommandExecutor
from app.services.fix_pipeline.workspace import resolve_repo_file

MAX_VERIFICATION_CONTRACT_RETRIES = 1
MAX_VERIFICATION_OUTPUT_LENGTH = 4_000
RUFF_COMMAND = "ruff"
BANDIT_COMMAND = "bandit"
SENSITIVE_FIELDS = {"cost_price", "role", "is_admin", "is_active"}
RESPONSE_SENSITIVE_FIELDS = {
    "cost_price",
    "hashed_password",
    "is_admin",
    "password",
    "role",
    "secret",
}
SENSITIVE_ROUTE_TERMS = {"admin", "cost", "margin", "profit", "report"}
ADMIN_DEPENDENCY_TERMS = {"require_admin", "get_current_admin", "admin_required"}

Verifier = Callable[[Path, FixIssueSpec, FixIssuePlan, int], FixIssueResult]


async def verify_fix_issues(
    *,
    sandbox_path: Path,
    issues: list[ReviewIssue],
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    timeout_seconds: int,
    attempt: int,
    executor: FixCommandExecutor,
    scenario_results_by_issue: dict[UUID, list[FixScenarioResult]] | None = None,
) -> list[FixIssueResult]:
    """Return exactly one post-patch verdict for every selected issue."""

    issues_by_id = {issue.id: issue for issue in issues}
    specs_by_id = {spec.issue_id: spec for spec in specs}
    results: dict[UUID, FixIssueResult] = {}
    fallback_specs: list[FixIssueSpec] = []
    fallback_plans: list[FixIssuePlan] = []
    for plan in plans:
        spec = specs_by_id[plan.issue_id]
        issue = issues_by_id[plan.issue_id]
        if plan.status != FixIssuePlanStatus.PLANNED:
            results[plan.issue_id] = _uncertain_plan_result(plan, attempt)
            continue
        static_result = _verify_static_finding(
            sandbox_path=sandbox_path,
            issue=issue,
            spec=spec,
            plan=plan,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
            executor=executor,
        )
        if static_result is not None:
            results[plan.issue_id] = static_result
            continue
        verifier = DEDICATED_VERIFIERS.get(spec.probe_id or "")
        if verifier is not None:
            results[plan.issue_id] = verifier(sandbox_path, spec, plan, attempt)
            continue
        fallback_specs.append(spec)
        fallback_plans.append(plan)

    if fallback_specs:
        fallback_results = await _verify_with_llm_contract(
            sandbox_path=sandbox_path,
            specs=fallback_specs,
            plans=fallback_plans,
            attempt=attempt,
        )
        results.update({result.issue_id: result for result in fallback_results})

    requested_ids = [plan.issue_id for plan in plans]
    if set(results) != set(requested_ids):
        raise FixPipelineError("Semantic verifier did not cover every selected issue")
    ordered_results = [results[issue_id] for issue_id in requested_ids]
    if scenario_results_by_issue is None:
        return ordered_results
    return [
        _enforce_scenario_contract(
            result,
            scenario_results_by_issue.get(result.issue_id, []),
        )
        for result in ordered_results
    ]


def _enforce_scenario_contract(
    result: FixIssueResult,
    scenario_results: list[FixScenarioResult],
) -> FixIssueResult:
    """Require executable exploit and preservation evidence before `fixed`."""

    required = [
        scenario
        for scenario in scenario_results
        if scenario.kind
        in {FixScenarioKind.EXPLOIT, FixScenarioKind.PRESERVED_BEHAVIOR}
    ]
    has_unavailable_check = (
        not scenario_results
        or not required
        or any(
            scenario.baseline_status
            in {FixScenarioStatus.SKIPPED, FixScenarioStatus.NOT_RUN}
            or scenario.patched_status
            in {FixScenarioStatus.SKIPPED, FixScenarioStatus.NOT_RUN}
            for scenario in scenario_results
        )
    )

    exploit_results = [
        scenario for scenario in required if scenario.kind == FixScenarioKind.EXPLOIT
    ]
    preserved_results = [
        scenario
        for scenario in required
        if scenario.kind == FixScenarioKind.PRESERVED_BEHAVIOR
    ]
    missing_behavior_dimension = not exploit_results or not preserved_results
    exploit_not_reproduced = bool(exploit_results) and any(
        scenario.baseline_status != FixScenarioStatus.FAILED
        for scenario in exploit_results
    )
    base_behavior_unhealthy = bool(preserved_results) and any(
        scenario.baseline_status != FixScenarioStatus.PASSED
        for scenario in preserved_results
    )
    patched_behavior_failed = bool(required) and any(
        scenario.patched_status != FixScenarioStatus.PASSED for scenario in required
    )

    related_regression = any(
        scenario.kind == FixScenarioKind.RELATED_TEST
        and scenario.baseline_status == FixScenarioStatus.PASSED
        and scenario.patched_status == FixScenarioStatus.FAILED
        for scenario in scenario_results
    )
    decisions = [
        (
            has_unavailable_check,
            FixIssueVerdict.UNCERTAIN,
            "Required verification could not run deterministically.",
        ),
        (
            missing_behavior_dimension,
            FixIssueVerdict.UNCERTAIN,
            "Exploit and preserved-behavior evidence are both required.",
        ),
        (
            exploit_not_reproduced,
            FixIssueVerdict.UNCERTAIN,
            "The exploit test did not reproduce the original bug.",
        ),
        (
            base_behavior_unhealthy,
            FixIssueVerdict.UNCERTAIN,
            "The preserved behavior was not healthy on the base commit.",
        ),
        (
            patched_behavior_failed,
            FixIssueVerdict.UNRESOLVED,
            "The patch failed an exploit or preserved-behavior scenario.",
        ),
        (
            related_regression,
            FixIssueVerdict.UNRESOLVED,
            "The patch introduced a failure in a related existing test.",
        ),
    ]
    result.scenario_results = scenario_results
    decision = next((item for item in decisions if item[0]), None)
    if decision is not None:
        result.verdict = decision[1]
        result.summary = decision[2]
    elif result.verdict != FixIssueVerdict.UNRESOLVED:
        result.verdict = FixIssueVerdict.FIXED
        result.summary = (
            "Exploit reproduction, patched behavior, and related regression "
            "checks passed."
        )
    return result


def merge_validation_results(
    command_result: FixValidationResult,
    issue_results: list[FixIssueResult],
) -> FixValidationResult:
    """Combine command checks and issue verdicts into one publish gate."""

    semantic_checks = [_semantic_check(result) for result in issue_results]
    checks = [*command_result.checks, *semantic_checks]
    has_failure = any(
        check.status == FixValidationCheckStatus.FAILED
        or (check.required and check.status == FixValidationCheckStatus.SKIPPED)
        for check in checks
    )
    status = (
        FixValidationStatus.FAILED
        if has_failure
        else FixValidationStatus.PASSED
        if checks
        else FixValidationStatus.NOT_RUN
    )
    return FixValidationResult(
        status=status,
        summary=build_fix_validation_summary(status, checks),
        checks=checks,
    )


def _verify_static_finding(
    *,
    sandbox_path: Path,
    issue: ReviewIssue,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    timeout_seconds: int,
    attempt: int,
    executor: FixCommandExecutor,
) -> FixIssueResult | None:
    if issue.source == IssueSource.RUFF and spec.rule_id:
        return _run_static_rule_check(
            executable=RUFF_COMMAND,
            command=[RUFF_COMMAND, "check", "--select", spec.rule_id, spec.file_path],
            sandbox_path=sandbox_path,
            spec=spec,
            plan=plan,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
            executor=executor,
        )
    if issue.source == IssueSource.BANDIT and spec.rule_id:
        return _run_static_rule_check(
            executable=BANDIT_COMMAND,
            command=[BANDIT_COMMAND, "-q", "-t", spec.rule_id, spec.file_path],
            sandbox_path=sandbox_path,
            spec=spec,
            plan=plan,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
            executor=executor,
        )
    return None


def _run_static_rule_check(
    *,
    executable: str,
    command: list[str],
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    timeout_seconds: int,
    attempt: int,
    executor: FixCommandExecutor,
) -> FixIssueResult:
    exit_code, stdout, stderr, _duration_ms = run_safe_command(
        command,
        cwd=sandbox_path,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    if exit_code == 0:
        return _result(
            spec,
            plan,
            FixIssueVerdict.FIXED,
            f"{spec.rule_id} no longer reports the selected file.",
            attempt,
            rationale="The original static analyzer rule no longer reports.",
        )
    output = (stdout + "\n" + stderr)[-MAX_VERIFICATION_OUTPUT_LENGTH:]
    if exit_code not in {1}:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            f"{executable} could not run in the isolated executor: {output.strip()}",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.UNRESOLVED,
        f"{spec.rule_id} still reports after patch: {output.strip()}",
        attempt,
    )


def _verify_jwt(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    decode_calls = []
    unsafe_reasons: list[str] = []
    for path, tree, content in _python_context(sandbox_path, plan):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _call_name(node.func).endswith(
                "jwt.decode"
            ):
                continue
            decode_calls.append((path, node.lineno))
            algorithms = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "algorithms"
                ),
                None,
            )
            if algorithms is None:
                unsafe_reasons.append(
                    f"{path}:{node.lineno} has no algorithms allowlist"
                )
                continue
            rendered = ast.get_source_segment(content, algorithms) or ""
            if re.search(r"['\"]none['\"]", rendered, re.IGNORECASE):
                unsafe_reasons.append(f"{path}:{node.lineno} allows the none algorithm")
    if not decode_calls:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No JWT decode call was available in the bounded verification context.",
            attempt,
        )
    if unsafe_reasons:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "; ".join(unsafe_reasons),
            attempt,
        )
    combined_content = _combined_context(sandbox_path, plan)
    handles_decode_errors = "except" in combined_content and any(
        error_name in combined_content
        for error_name in ("JWTError", "JWTClaimsError", "ExpiredSignatureError")
    )
    if not handles_decode_errors:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "JWT decode errors are not translated into an authentication failure.",
            attempt,
        )
    path, line = decode_calls[0]
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Every observed JWT decode call has an explicit allowlist without none.",
        attempt,
        path=path,
        line=line,
        rationale="JWT verification uses an explicit algorithm allowlist.",
    )


def _verify_mass_assignment(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    unsafe_fields: list[str] = []
    inspected = False
    for path, tree, _content in _python_context(sandbox_path, plan):
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not _is_pydantic_model(node):
                continue
            if "admin" in node.name.casefold() or "internal" in node.name.casefold():
                continue
            request_terms = ("create", "update", "input", "request")
            if not any(term in node.name.casefold() for term in request_terms):
                continue
            inspected = True
            fields = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
            }
            for field in sorted(fields & SENSITIVE_FIELDS):
                unsafe_fields.append(f"{path}:{node.lineno} {node.name}.{field}")
    if unsafe_fields:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Sensitive fields remain in a non-admin request model: "
            + ", ".join(unsafe_fields),
            attempt,
        )
    verdict = FixIssueVerdict.FIXED if inspected else FixIssueVerdict.UNCERTAIN
    summary = (
        "Non-admin request models in context do not declare sensitive fields."
        if inspected
        else "No relevant request model was available in the bounded context."
    )
    return _result(spec, plan, verdict, summary, attempt)


def _verify_inventory(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    content = _combined_context(sandbox_path, plan).casefold()
    has_mutation = "quantity" in content and any(
        token in content for token in ("+=", "-=", "delta", "adjust_stock")
    )
    has_lower_bound = any(
        pattern in content
        for pattern in (
            "quantity + delta < 0",
            "new_quantity < 0",
            "updated_quantity < 0",
            "quantity >= 0",
            "checkconstraint",
        )
    )
    has_concurrency_guard = any(
        pattern in content
        for pattern in (
            "with_for_update",
            "quantity = quantity +",
            "checkconstraint",
            "returning(",
        )
    )
    if not has_mutation:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No inventory mutation was available in the bounded context.",
            attempt,
        )
    if not has_lower_bound or not has_concurrency_guard:
        missing = []
        if not has_lower_bound:
            missing.append("non-negative lower-bound enforcement")
        if not has_concurrency_guard:
            missing.append("atomic update, row lock, or database constraint")
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Inventory mutation is missing " + " and ".join(missing) + ".",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Inventory mutation enforces the lower bound and protects read-modify-write.",
        attempt,
    )


def _verify_sensitive_response(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    models, routes = _collect_response_models_and_routes(sandbox_path, plan)
    exposed: list[str] = []
    for path, line, response_names, function_source in routes:
        is_admin = any(term in function_source for term in ADMIN_DEPENDENCY_TERMS)
        if is_admin:
            continue
        for model_name, fields in models.items():
            sensitive_fields = fields & RESPONSE_SENSITIVE_FIELDS
            if model_name in response_names and sensitive_fields:
                exposed.append(
                    f"{path}:{line} {model_name} exposes "
                    + ", ".join(sorted(sensitive_fields))
                )
    if exposed:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Non-admin response exposure remains: " + "; ".join(exposed),
            attempt,
        )
    if not routes:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No route response model was available in the bounded context.",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Observed non-admin response models do not expose sensitive fields.",
        attempt,
    )


def _collect_response_models_and_routes(
    sandbox_path: Path,
    plan: FixIssuePlan,
) -> tuple[dict[str, set[str]], list[tuple[str, int, str, str]]]:
    models: dict[str, set[str]] = {}
    routes: list[tuple[str, int, str, str]] = []
    for path, tree, content in _python_context(sandbox_path, plan):
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and _is_pydantic_model(node):
                models[node.name] = {
                    child.target.id
                    for child in node.body
                    if isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                }
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_source = ast.get_source_segment(content, node) or ""
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    response_model = next(
                        (
                            keyword.value
                            for keyword in decorator.keywords
                            if keyword.arg == "response_model"
                        ),
                        None,
                    )
                    if response_model is None:
                        continue
                    routes.append(
                        (
                            path,
                            node.lineno,
                            _annotation_names(response_model),
                            function_source.casefold(),
                        )
                    )
    return models, routes


def _verify_randomness(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    content = _combined_context(sandbox_path, plan)
    predictable = re.search(
        r"random\.(seed|random|randint|choice|choices|randrange)|time\.time\(",
        content,
        re.IGNORECASE,
    )
    secure = re.search(r"secrets\.(token_|choice|randbelow)|SystemRandom", content)
    if predictable:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Predictable randomness remains in the security-sensitive context.",
            attempt,
        )
    if not secure:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No cryptographic randomness call was observed after the patch.",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Security-sensitive randomness uses a cryptographic generator.",
        attempt,
    )


def _verify_open_redirect(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    content = _combined_context(sandbox_path, plan).casefold()
    has_redirect = "redirectresponse" in content or "redirect(" in content
    has_url_validation = all(
        token in content for token in ("urlparse", "netloc", "scheme")
    )
    blocks_protocol_relative = any(
        pattern in content for pattern in ('startswith("//")', "startswith('//')")
    )
    blocks_backslash = "\\\\" in content or "backslash" in content
    if not has_redirect:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No redirect sink was available in the bounded context.",
            attempt,
        )
    if not (has_url_validation and blocks_protocol_relative and blocks_backslash):
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Redirect validation does not clearly block absolute, protocol-relative, "
            "and backslash destinations.",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Redirect validation restricts destinations to safe local paths.",
        attempt,
    )


def _verify_rbac(
    sandbox_path: Path,
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    attempt: int,
) -> FixIssueResult:
    sensitive_routes = 0
    unguarded: list[str] = []
    for path, tree, content in _python_context(sandbox_path, plan):
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route_text = " ".join(
                ast.get_source_segment(content, decorator) or ""
                for decorator in node.decorator_list
            ).casefold()
            function_source = (ast.get_source_segment(content, node) or "").casefold()
            route_identity = f"{node.name} {route_text}"
            if not any(term in route_identity for term in SENSITIVE_ROUTE_TERMS):
                continue
            sensitive_routes += 1
            if not any(term in function_source for term in ADMIN_DEPENDENCY_TERMS):
                unguarded.append(f"{path}:{node.lineno} {node.name}")
    if unguarded:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNRESOLVED,
            "Sensitive routes lack an admin dependency: " + ", ".join(unguarded),
            attempt,
        )
    if not sensitive_routes:
        return _result(
            spec,
            plan,
            FixIssueVerdict.UNCERTAIN,
            "No sensitive route was available in the bounded context.",
            attempt,
        )
    return _result(
        spec,
        plan,
        FixIssueVerdict.FIXED,
        "Every sensitive route in context requires an admin dependency.",
        attempt,
    )


DEDICATED_VERIFIERS: dict[str, Verifier] = {
    "security.jwt_algorithm_allowlist": _verify_jwt,
    "security.mass_assignment": _verify_mass_assignment,
    "bug.inventory_invariant": _verify_inventory,
    "security.sensitive_response_exposure": _verify_sensitive_response,
    "security.insecure_randomness": _verify_randomness,
    "security.open_redirect": _verify_open_redirect,
    "security.role_authorization": _verify_rbac,
}


async def _verify_with_llm_contract(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    attempt: int,
) -> list[FixIssueResult]:
    requested_ids = [spec.issue_id for spec in specs]
    for contract_attempt in range(MAX_VERIFICATION_CONTRACT_RETRIES + 1):
        response = await _request_llm_verification(
            sandbox_path=sandbox_path,
            specs=specs,
            plans=plans,
            attempt=attempt,
            retry=contract_attempt > 0,
        )
        if _has_valid_llm_results(response.results, requested_ids, plans):
            ordered_results = _ordered_results(response.results, requested_ids)
            return _normalize_llm_results(
                ordered_results,
                specs=specs,
                plans=plans,
                attempt=attempt,
            )

    results: list[FixIssueResult] = []
    for spec, plan in zip(specs, plans, strict=True):
        response = await _request_llm_verification(
            sandbox_path=sandbox_path,
            specs=[spec],
            plans=[plan],
            attempt=attempt,
            retry=True,
        )
        if not _has_valid_llm_results(
            response.results,
            [spec.issue_id],
            [plan],
        ):
            raise FixPipelineError(
                f"Semantic verifier contract failed for issue {spec.issue_id}"
            )
        results.extend(
            _normalize_llm_results(
                response.results,
                specs=[spec],
                plans=[plan],
                attempt=attempt,
            )
        )
    return results


async def _request_llm_verification(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    attempt: int,
    retry: bool,
) -> FixVerificationResponse:
    from app.ai.llm.config import run_with_configured_llm

    plans_by_id = {plan.issue_id: plan for plan in plans}
    payload: list[dict[str, object]] = []
    for spec in specs:
        plan = plans_by_id[spec.issue_id]
        source_files = {
            path: resolve_repo_file(sandbox_path, path).read_text(encoding="utf-8")
            for path in list(dict.fromkeys([*plan.context_files, *plan.editable_files]))
        }
        payload.append(
            {
                "issue": spec.model_dump(mode="json", exclude={"source_files"}),
                "plan": plan.model_dump(mode="json"),
                "post_patch_source": source_files,
            }
        )

    async def call(llm: Any) -> FixVerificationResponse:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You are a strict post-patch semantic verifier. A changed diff "
                    "is not proof of correctness. Return only JSON.",
                ),
                (
                    "human",
                    _build_verification_prompt(payload, attempt=attempt, retry=retry),
                ),
            ]
        )
        parsed = parse_json_object_text(_message_content(result))
        if parsed is None:
            raise ValueError("Fix verification JSON object was not found")
        return FixVerificationResponse.model_validate(parsed)

    return await run_with_configured_llm(call)


def _build_verification_prompt(
    payload: list[dict[str, object]], *, attempt: int, retry: bool
) -> str:
    retry_text = "Previous output violated the exact-ID contract. " if retry else ""
    return (
        f"{retry_text}Return an object with results containing exactly one result per "
        "requested issue_id and no unknown IDs. verdict must be fixed, unresolved, or "
        "uncertain. fixed requires positive post-patch evidence that every safety "
        "property and acceptance check holds. Include issue_id, probe_id, verdict, "
        "summary, planned_files, changed_files, verification_attempts, and bounded "
        "evidence references. uncertain never means fixed. "
        f"Set verification_attempts to {attempt}.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _uncertain_plan_result(plan: FixIssuePlan, attempt: int) -> FixIssueResult:
    return FixIssueResult(
        issue_id=plan.issue_id,
        probe_id=plan.probe_id,
        verdict=FixIssueVerdict.UNCERTAIN,
        summary=plan.reason or "No safe fix plan was available.",
        planned_files=plan.editable_files,
        changed_files=[],
        verification_attempts=attempt,
    )


def _result(
    spec: FixIssueSpec,
    plan: FixIssuePlan,
    verdict: FixIssueVerdict,
    summary: str,
    attempt: int,
    *,
    path: str | None = None,
    line: int | None = None,
    rationale: str | None = None,
) -> FixIssueResult:
    evidence = []
    if verdict == FixIssueVerdict.FIXED and path is None:
        path = spec.file_path
        rationale = summary
    if path is not None and rationale is not None:
        evidence.append(
            FixEvidenceReference(
                file_path=path,
                line_start=line,
                line_end=line,
                rationale=rationale,
            )
        )
    return FixIssueResult(
        issue_id=spec.issue_id,
        probe_id=spec.probe_id,
        verdict=verdict,
        summary=summary,
        planned_files=plan.editable_files,
        changed_files=plan.editable_files,
        verification_attempts=attempt,
        evidence=evidence,
    )


def _semantic_check(result: FixIssueResult) -> FixValidationCheck:
    passed = result.verdict == FixIssueVerdict.FIXED
    return FixValidationCheck(
        name=f"semantic:{result.issue_id}",
        command=f"semantic verifier:{result.probe_id or 'fallback'}",
        kind=FixValidationCheckKind.SEMANTIC,
        status=(
            FixValidationCheckStatus.PASSED
            if passed
            else FixValidationCheckStatus.FAILED
        ),
        exit_code=0 if passed else 1,
        stdout=result.summary if passed else "",
        stderr="" if passed else result.summary,
        duration_ms=0,
    )


def _python_context(
    sandbox_path: Path, plan: FixIssuePlan
) -> list[tuple[str, ast.Module, str]]:
    context: list[tuple[str, ast.Module, str]] = []
    for path in list(dict.fromkeys([*plan.context_files, *plan.editable_files])):
        if not path.endswith(".py"):
            continue
        file_path = resolve_repo_file(sandbox_path, path)
        content = file_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        context.append((path, tree, content))
    return context


def _combined_context(sandbox_path: Path, plan: FixIssuePlan) -> str:
    return "\n".join(
        resolve_repo_file(sandbox_path, path).read_text(encoding="utf-8")
        for path in list(dict.fromkeys([*plan.context_files, *plan.editable_files]))
    )


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    return any(_call_name(base).endswith("BaseModel") for base in node.bases)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return _call_name(node.value)
    return ""


def _annotation_names(node: ast.AST) -> str:
    return " ".join(
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    )


def _has_exact_results(
    results: list[FixIssueResult], requested_ids: list[UUID]
) -> bool:
    received_ids = [result.issue_id for result in results]
    return len(received_ids) == len(requested_ids) and set(received_ids) == set(
        requested_ids
    )


def _has_valid_llm_results(
    results: list[FixIssueResult],
    requested_ids: list[UUID],
    plans: list[FixIssuePlan],
) -> bool:
    if not _has_exact_results(results, requested_ids):
        return False
    plans_by_id = {plan.issue_id: plan for plan in plans}
    for result in results:
        plan = plans_by_id[result.issue_id]
        known_paths = _plan_source_paths(plan)
        if result.verdict == FixIssueVerdict.FIXED and not result.evidence:
            return False
        if any(reference.file_path not in known_paths for reference in result.evidence):
            return False
    return True


def _normalize_llm_results(
    results: list[FixIssueResult],
    *,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    attempt: int,
) -> list[FixIssueResult]:
    specs_by_id = {spec.issue_id: spec for spec in specs}
    plans_by_id = {plan.issue_id: plan for plan in plans}
    for result in results:
        spec = specs_by_id[result.issue_id]
        plan = plans_by_id[result.issue_id]
        result.probe_id = spec.probe_id
        result.planned_files = plan.editable_files
        result.changed_files = [
            path for path in result.changed_files if path in plan.editable_files
        ]
        result.verification_attempts = attempt
    return results


def _plan_source_paths(plan: FixIssuePlan) -> set[str]:
    return set(plan.context_files) | set(plan.editable_files)


def _ordered_results(
    results: list[FixIssueResult], requested_ids: list[UUID]
) -> list[FixIssueResult]:
    results_by_id = {result.issue_id: result for result in results}
    return [results_by_id[issue_id] for issue_id in requested_ids]


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
