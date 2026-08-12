"""Independent temporary tests for exploit and behavior-preservation contracts."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.ai.json_utils import parse_json_object_text
from app.analyzers.secret_scanner import mask_secret_values
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixScenarioKind,
    FixScenarioResult,
    FixScenarioStatus,
    FixVerificationScenario,
)
from app.services.fix_pipeline.contracts import (
    FixEnvironmentStatus,
    FixGeneratedTest,
    FixIssueSpec,
    FixProjectEnvironment,
    FixTestArtifact,
    FixTestFramework,
    FixTestGenerationResponse,
    FixVerificationContext,
)
from app.services.fix_pipeline.dependencies import (
    prepare_project_environments,
    run_safe_command,
)
from app.services.fix_pipeline.errors import FixPipelineError
from app.services.fix_pipeline.execution import FixCommandExecutor
from app.services.fix_pipeline.workspace import (
    get_changed_files,
    resolve_repo_file,
    restore_index_files,
)

MAX_TEST_SOURCE_BYTES = 40_000
MAX_RELATED_TESTS_PER_ISSUE = 8
MAX_RELATED_TEST_BYTES = 80_000
MAX_TEST_GENERATION_RETRIES = 1
MIN_RELATED_TOKEN_LENGTH = 3
MAX_SCENARIO_OUTPUT_LENGTH = 4_000
TEST_FAILURE_EXIT_CODE = 1
TEMP_TEST_DIRECTORY = ".repoguard-tests"
TEST_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
}
TEST_INFRASTRUCTURE_FAILURE_PATTERNS = (
    "cannot find module",
    "collection error",
    "command not found",
    "error while importing test module",
    "failed to load config",
    "failed to resolve import",
    "internal error",
    "module not found",
    "no tests found",
    "syntaxerror",
)


async def prepare_fix_verification(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> FixVerificationContext:
    """Prepare locked environments, hidden tests, and immutable baseline results."""

    planned = [plan for plan in plans if plan.status == FixIssuePlanStatus.PLANNED]
    environments = prepare_project_environments(
        sandbox_path=sandbox_path,
        plans=planned,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    context = FixVerificationContext(project_environments=environments)
    specs_by_id = {spec.issue_id: spec for spec in specs}
    runnable_scenarios = _runnable_scenarios(
        sandbox_path=sandbox_path,
        plans=planned,
        environments=environments,
    )
    generated_tests: list[FixGeneratedTest] = []
    if runnable_scenarios:
        try:
            generated_tests = await _request_tests_with_contract(
                sandbox_path=sandbox_path,
                specs=specs,
                plans=planned,
                runnable_scenarios=runnable_scenarios,
                environments=environments,
            )
        except (FixPipelineError, ValidationError, ValueError) as error:
            _record_unavailable_scenarios(
                context=context,
                plans=planned,
                environments=environments,
                sandbox_path=sandbox_path,
                reason=f"Temporary test generation failed: {error}",
            )
            return context

    tests_by_key = {(test.issue_id, test.scenario_id): test for test in generated_tests}
    for plan in planned:
        spec = specs_by_id[plan.issue_id]
        context.related_tests[plan.issue_id] = _find_related_tests(
            sandbox_path=sandbox_path,
            plan=plan,
        )
        results: list[FixScenarioResult] = []
        for scenario in _plan_scenarios(plan):
            environment = _environment_for_scenario(
                sandbox_path=sandbox_path,
                plan=plan,
                scenario=scenario,
                environments=environments,
            )
            if environment is None or environment.status != FixEnvironmentStatus.READY:
                results.append(
                    _skipped_result(
                        scenario,
                        environment,
                        reason=(
                            environment.reason
                            if environment is not None
                            else (
                                "No supported project environment matched the scenario."
                            )
                        ),
                    )
                )
                continue
            generated = tests_by_key.get((plan.issue_id, scenario.scenario_id))
            if generated is None:
                results.append(
                    _skipped_result(
                        scenario,
                        environment,
                        reason="The test generator omitted this required scenario.",
                    )
                )
                continue
            artifact = _write_test_artifact(
                issue_id=plan.issue_id,
                scenario=scenario,
                generated=generated,
                environment=environment,
            )
            context.artifacts.append(artifact)
            baseline_status, output = _run_artifact(
                artifact,
                environment=environment,
                sandbox_path=sandbox_path,
                plan=plan,
                timeout_seconds=timeout_seconds,
                executor=executor,
            )
            results.append(
                FixScenarioResult(
                    scenario_id=scenario.scenario_id,
                    kind=scenario.kind,
                    framework=environment.framework.value
                    if environment.framework
                    else None,
                    baseline_status=baseline_status,
                    output=output,
                )
            )

        results.extend(
            _run_related_tests(
                sandbox_path=sandbox_path,
                plan=plan,
                paths=context.related_tests[plan.issue_id],
                environments=environments,
                timeout_seconds=timeout_seconds,
                baseline=True,
                executor=executor,
            )
        )
        context.baseline_results[spec.issue_id] = results
    return context


def run_patched_verification(
    *,
    sandbox_path: Path,
    plans: list[FixIssuePlan],
    context: FixVerificationContext,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> dict[UUID, list[FixScenarioResult]]:
    """Run the immutable temporary and related tests against the current patch."""

    artifacts_by_key = {
        (artifact.issue_id, artifact.scenario_id): artifact
        for artifact in context.artifacts
    }
    plans_by_id = {plan.issue_id: plan for plan in plans}
    results_by_issue: dict[UUID, list[FixScenarioResult]] = {}
    for issue_id, baseline_results in context.baseline_results.items():
        plan = plans_by_id[issue_id]
        patched_results: list[FixScenarioResult] = []
        for baseline_result in baseline_results:
            if baseline_result.kind == FixScenarioKind.RELATED_TEST:
                continue
            artifact = artifacts_by_key.get((issue_id, baseline_result.scenario_id))
            if artifact is None:
                patched_results.append(baseline_result.model_copy(deep=True))
                continue
            environment = context.project_environments[artifact.project_root]
            if not _materialize_artifact(artifact):
                patched_results.append(
                    baseline_result.model_copy(
                        update={
                            "patched_status": FixScenarioStatus.SKIPPED,
                            "output": (
                                "Temporary verification test changed after generation."
                            ),
                        }
                    )
                )
                continue
            patched_status, output = _run_artifact(
                artifact,
                environment=environment,
                sandbox_path=sandbox_path,
                plan=plan,
                timeout_seconds=timeout_seconds,
                executor=executor,
            )
            patched_results.append(
                baseline_result.model_copy(
                    update={"patched_status": patched_status, "output": output}
                )
            )

        patched_results.extend(
            _run_related_tests(
                sandbox_path=sandbox_path,
                plan=plan,
                paths=context.related_tests.get(issue_id, []),
                environments=context.project_environments,
                timeout_seconds=timeout_seconds,
                baseline=False,
                baseline_results=baseline_results,
                executor=executor,
            )
        )
        results_by_issue[issue_id] = patched_results
    return results_by_issue


def cleanup_temporary_tests(context: FixVerificationContext) -> None:
    """Remove generated tests while leaving dependency caches for TTL cleanup."""

    directories = {artifact.file_path.parent for artifact in context.artifacts}
    sorted_directories = sorted(
        directories,
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in sorted_directories:
        test_root = next(
            (
                parent
                for parent in [directory, *directory.parents]
                if parent.name == TEMP_TEST_DIRECTORY
            ),
            None,
        )
        if test_root is not None and test_root.is_dir():
            shutil.rmtree(test_root)


async def _request_tests_with_contract(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    runnable_scenarios: dict[tuple[UUID, str], FixProjectEnvironment],
    environments: dict[Path, FixProjectEnvironment],
) -> list[FixGeneratedTest]:
    expected_keys = set(runnable_scenarios)
    retry_reason: str | None = None
    for _attempt in range(MAX_TEST_GENERATION_RETRIES + 1):
        response = await _request_tests(
            sandbox_path=sandbox_path,
            specs=specs,
            plans=plans,
            runnable_scenarios=runnable_scenarios,
            environments=environments,
            retry_reason=retry_reason,
        )
        received_keys = {(test.issue_id, test.scenario_id) for test in response.tests}
        valid_content = all(
            test.content.strip()
            and len(test.content.encode("utf-8")) <= MAX_TEST_SOURCE_BYTES
            for test in response.tests
        )
        if (
            received_keys == expected_keys
            and len(received_keys) == len(response.tests)
            and valid_content
        ):
            return response.tests
        retry_reason = (
            "Return exactly one bounded, non-empty test for every requested "
            "issue_id/scenario_id pair."
        )
    raise FixPipelineError("Temporary test generation violated its response contract")


async def _request_tests(
    *,
    sandbox_path: Path,
    specs: list[FixIssueSpec],
    plans: list[FixIssuePlan],
    runnable_scenarios: dict[tuple[UUID, str], FixProjectEnvironment],
    environments: dict[Path, FixProjectEnvironment],
    retry_reason: str | None,
) -> FixTestGenerationResponse:
    from app.ai.llm.config import run_with_configured_llm

    specs_by_id = {spec.issue_id: spec for spec in specs}
    payload: list[dict[str, object]] = []
    for plan in plans:
        scenarios = [
            scenario
            for scenario in _plan_scenarios(plan)
            if (plan.issue_id, scenario.scenario_id) in runnable_scenarios
        ]
        if not scenarios:
            continue
        source_files = {
            path: resolve_repo_file(sandbox_path, path).read_text(encoding="utf-8")
            for path in list(dict.fromkeys([*plan.context_files, *plan.editable_files]))
        }
        payload.append(
            {
                "issue": specs_by_id[plan.issue_id].model_dump(
                    mode="json", exclude={"source_files"}
                ),
                "plan": plan.model_dump(mode="json"),
                "scenarios": [
                    scenario.model_dump(mode="json") for scenario in scenarios
                ],
                "frameworks": _scenario_frameworks(
                    issue_id=plan.issue_id,
                    scenarios=scenarios,
                    runnable_scenarios=runnable_scenarios,
                ),
                "source_files": source_files,
            }
        )

    async def call(llm: Any) -> FixTestGenerationResponse:
        result = await llm.ainvoke(
            [
                (
                    "system",
                    "You independently create deterministic regression tests from "
                    "behavior contracts. You do not implement the fix. Return only "
                    "JSON.",
                ),
                (
                    "human",
                    _build_test_prompt(payload, retry_reason=retry_reason),
                ),
            ]
        )
        parsed = parse_json_object_text(_message_content(result))
        if parsed is None:
            raise ValueError("Temporary test generation JSON object was not found")
        return FixTestGenerationResponse.model_validate(parsed)

    del environments
    return await run_with_configured_llm(call)


def _build_test_prompt(
    payload: list[dict[str, object]], *, retry_reason: str | None
) -> str:
    retry_text = f"Previous response error: {retry_reason}\n" if retry_reason else ""
    return (
        f"{retry_text}Return an object with a tests array. Each test must contain "
        "issue_id, scenario_id, and complete test-file content. Return exactly one "
        "test per supplied scenario and no unknown IDs. Use only the supplied pytest, "
        "vitest, or jest framework. An exploit test must fail against the vulnerable "
        "source and pass only when the exploit is blocked. A preserved_behavior test "
        "must pass before and after the fix. Exercise public behavior where possible, "
        "avoid network/time/random dependencies, do not edit source, and do not weaken "
        "assertions based on implementation details.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _runnable_scenarios(
    *,
    sandbox_path: Path,
    plans: list[FixIssuePlan],
    environments: dict[Path, FixProjectEnvironment],
) -> dict[tuple[UUID, str], FixProjectEnvironment]:
    result: dict[tuple[UUID, str], FixProjectEnvironment] = {}
    for plan in plans:
        for scenario in _plan_scenarios(plan):
            environment = _environment_for_scenario(
                sandbox_path=sandbox_path,
                plan=plan,
                scenario=scenario,
                environments=environments,
            )
            if (
                environment is not None
                and environment.status == FixEnvironmentStatus.READY
            ):
                result[(plan.issue_id, scenario.scenario_id)] = environment
    return result


def _environment_for_scenario(
    *,
    sandbox_path: Path,
    plan: FixIssuePlan,
    scenario: FixVerificationScenario,
    environments: dict[Path, FixProjectEnvironment],
) -> FixProjectEnvironment | None:
    candidate_paths = [*scenario.related_files, *plan.editable_files]
    for relative_path in candidate_paths:
        try:
            file_path = resolve_repo_file(sandbox_path, relative_path)
        except FixPipelineError:
            continue
        matches = [
            environment
            for root, environment in environments.items()
            if file_path == root or root in file_path.parents
        ]
        if matches:
            return max(
                matches,
                key=lambda environment: len(environment.project_root.parts),
            )
    return None


def _write_test_artifact(
    *,
    issue_id: UUID,
    scenario: FixVerificationScenario,
    generated: FixGeneratedTest,
    environment: FixProjectEnvironment,
) -> FixTestArtifact:
    if environment.framework is None:
        raise FixPipelineError("Runnable environment has no test framework")
    safe_scenario_id = re.sub(r"[^a-zA-Z0-9_-]", "_", scenario.scenario_id)[:80]
    suffix = _test_file_suffix(scenario, environment.framework)
    test_root = environment.project_root / TEMP_TEST_DIRECTORY
    if test_root.is_symlink():
        raise FixPipelineError("Temporary test directory cannot be a symlink")
    file_path = test_root / issue_id.hex / f"test_{safe_scenario_id}{suffix}"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if not file_path.resolve().is_relative_to(environment.project_root.resolve()):
        raise FixPipelineError("Temporary test path escapes the project root")
    file_path.write_text(generated.content, encoding="utf-8")
    return FixTestArtifact(
        issue_id=issue_id,
        scenario_id=scenario.scenario_id,
        framework=environment.framework,
        project_root=environment.project_root,
        file_path=file_path,
        content=generated.content,
    )


def _test_file_suffix(
    scenario: FixVerificationScenario,
    framework: FixTestFramework,
) -> str:
    if framework == FixTestFramework.PYTEST:
        return ".py"
    source_suffix = next(
        (
            Path(path).suffix
            for path in scenario.related_files
            if Path(path).suffix in TEST_SUFFIXES - {".py"}
        ),
        ".js",
    )
    return f".test{source_suffix}"


def _run_artifact(
    artifact: FixTestArtifact,
    *,
    environment: FixProjectEnvironment,
    sandbox_path: Path,
    plan: FixIssuePlan,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> tuple[FixScenarioStatus, str]:
    try:
        return _run_test_with_source_guard(
            path=artifact.file_path,
            environment=environment,
            sandbox_path=sandbox_path,
            plan=plan,
            timeout_seconds=timeout_seconds,
            executor=executor,
        )
    finally:
        artifact.file_path.unlink(missing_ok=True)


def _run_related_tests(
    *,
    sandbox_path: Path,
    plan: FixIssuePlan,
    paths: list[Path],
    environments: dict[Path, FixProjectEnvironment],
    timeout_seconds: int,
    baseline: bool,
    baseline_results: list[FixScenarioResult] | None = None,
    executor: FixCommandExecutor,
) -> list[FixScenarioResult]:
    previous_by_id = {result.scenario_id: result for result in (baseline_results or [])}
    results: list[FixScenarioResult] = []
    for path in paths:
        environment = _environment_for_path(path, environments)
        scenario_id = f"related:{path.relative_to(sandbox_path).as_posix()}"
        if environment is None or environment.status != FixEnvironmentStatus.READY:
            status = FixScenarioStatus.SKIPPED
            output = environment.reason if environment else "No test environment found."
        else:
            status, output = _run_test_with_source_guard(
                path=path,
                environment=environment,
                sandbox_path=sandbox_path,
                plan=plan,
                timeout_seconds=timeout_seconds,
                executor=executor,
            )
        if baseline:
            results.append(
                FixScenarioResult(
                    scenario_id=scenario_id,
                    kind=FixScenarioKind.RELATED_TEST,
                    framework=environment.framework.value
                    if environment and environment.framework
                    else None,
                    baseline_status=status,
                    output=output,
                )
            )
            continue
        previous = previous_by_id.get(scenario_id)
        results.append(
            FixScenarioResult(
                scenario_id=scenario_id,
                kind=FixScenarioKind.RELATED_TEST,
                framework=environment.framework.value
                if environment and environment.framework
                else None,
                baseline_status=(
                    previous.baseline_status
                    if previous is not None
                    else FixScenarioStatus.NOT_RUN
                ),
                patched_status=status,
                output=output,
            )
        )
    del plan
    return results


def _run_test_with_source_guard(
    *,
    path: Path,
    environment: FixProjectEnvironment,
    sandbox_path: Path,
    plan: FixIssuePlan,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> tuple[FixScenarioStatus, str]:
    protected_sources, previously_changed_files = _snapshot_plan_sources(
        sandbox_path,
        plan,
    )
    status, output = _run_test_path(
        path=path,
        environment=environment,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    changed_files = _restore_plan_sources(
        sandbox_path=sandbox_path,
        protected_sources=protected_sources,
        previously_changed_files=previously_changed_files,
    )
    if not changed_files:
        return status, output
    return (
        FixScenarioStatus.SKIPPED,
        _truncate_scenario_output(
            "Test execution modified protected source files; changes were restored: "
            + ", ".join(changed_files)
        ),
    )


def _snapshot_plan_sources(
    sandbox_path: Path,
    plan: FixIssuePlan,
) -> tuple[dict[Path, bytes], set[str]]:
    previously_changed_files = _get_tracked_changes(sandbox_path)
    protected_paths = [*_plan_source_paths(plan), *previously_changed_files]
    protected_sources = {
        file_path: file_path.read_bytes()
        for relative_path in dict.fromkeys(protected_paths)
        if (file_path := resolve_repo_file(sandbox_path, relative_path)).is_file()
    }
    return protected_sources, previously_changed_files


def _restore_plan_sources(
    *,
    sandbox_path: Path,
    protected_sources: dict[Path, bytes],
    previously_changed_files: set[str],
) -> list[str]:
    sandbox_root = sandbox_path.resolve()
    newly_changed_files = _get_tracked_changes(sandbox_path) - previously_changed_files
    changed_files: set[str] = set(newly_changed_files)
    for file_path, expected_content in protected_sources.items():
        if not file_path.parent.resolve().is_relative_to(sandbox_root):
            raise FixPipelineError(
                "Test execution moved a protected source directory outside the sandbox"
            )
        if file_path.is_dir() and not file_path.is_symlink():
            raise FixPipelineError(
                "Test execution replaced a protected source file with a directory"
            )
        current_content = (
            file_path.read_bytes()
            if file_path.is_file() and not file_path.is_symlink()
            else None
        )
        if current_content == expected_content:
            continue
        if file_path.is_symlink():
            file_path.unlink()
        file_path.write_bytes(expected_content)
        changed_files.add(file_path.relative_to(sandbox_root).as_posix())
    if newly_changed_files:
        restore_index_files(sandbox_path, sorted(newly_changed_files))
    return sorted(changed_files)


def _plan_source_paths(plan: FixIssuePlan) -> list[str]:
    return list(dict.fromkeys([*plan.context_files, *plan.editable_files]))


def _get_tracked_changes(sandbox_path: Path) -> set[str]:
    git_metadata = sandbox_path / ".git"
    if not git_metadata.exists():
        return set()
    return set(get_changed_files(sandbox_path))


def _truncate_scenario_output(output: str) -> str:
    return output[-MAX_SCENARIO_OUTPUT_LENGTH:]


def _run_test_path(
    *,
    path: Path,
    environment: FixProjectEnvironment,
    timeout_seconds: int,
    executor: FixCommandExecutor,
) -> tuple[FixScenarioStatus, str]:
    relative_path = path.relative_to(environment.project_root).as_posix()
    command = [*environment.runner_command]
    if environment.framework == FixTestFramework.PYTEST:
        command.extend([relative_path, "-q", "--disable-warnings", "--maxfail=1"])
    elif environment.framework == FixTestFramework.VITEST:
        command.extend(["run", relative_path])
    else:
        command.extend(["--runTestsByPath", relative_path, "--runInBand"])
    exit_code, stdout, stderr, _duration_ms = run_safe_command(
        command,
        cwd=environment.project_root,
        timeout_seconds=timeout_seconds,
        executor=executor,
    )
    output = mask_secret_values((stdout + "\n" + stderr).strip())
    if exit_code == 0:
        return FixScenarioStatus.PASSED, output
    normalized_output = output.casefold()
    if exit_code != TEST_FAILURE_EXIT_CODE or any(
        pattern in normalized_output for pattern in TEST_INFRASTRUCTURE_FAILURE_PATTERNS
    ):
        return FixScenarioStatus.SKIPPED, output
    return FixScenarioStatus.FAILED, output


def _find_related_tests(*, sandbox_path: Path, plan: FixIssuePlan) -> list[Path]:
    editable_paths = [
        resolve_repo_file(sandbox_path, path) for path in plan.editable_files
    ]
    tokens = {
        token
        for path in editable_paths
        for token in (
            path.stem,
            path.relative_to(sandbox_path).with_suffix("").as_posix().replace("/", "."),
        )
        if len(token) >= MIN_RELATED_TOKEN_LENGTH
    }
    scored: list[tuple[int, Path]] = []
    for path in sandbox_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEST_SUFFIXES:
            continue
        ignored_parts = {".git", "node_modules", ".venv", ".repoguard-env"}
        if any(part in ignored_parts for part in path.parts):
            continue
        if not _is_test_file(path) or path.stat().st_size > MAX_RELATED_TEST_BYTES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        score = sum(token in content for token in tokens)
        if score:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1].as_posix()))
    return [path for _score, path in scored[:MAX_RELATED_TESTS_PER_ISSUE]]


def _record_unavailable_scenarios(
    *,
    context: FixVerificationContext,
    plans: list[FixIssuePlan],
    environments: dict[Path, FixProjectEnvironment],
    sandbox_path: Path,
    reason: str,
) -> None:
    for plan in plans:
        context.baseline_results[plan.issue_id] = [
            _skipped_result(
                scenario,
                _environment_for_scenario(
                    sandbox_path=sandbox_path,
                    plan=plan,
                    scenario=scenario,
                    environments=environments,
                ),
                reason=reason,
            )
            for scenario in _plan_scenarios(plan)
        ]


def _skipped_result(
    scenario: FixVerificationScenario,
    environment: FixProjectEnvironment | None,
    *,
    reason: str,
) -> FixScenarioResult:
    return FixScenarioResult(
        scenario_id=scenario.scenario_id,
        kind=scenario.kind,
        framework=(
            environment.framework.value
            if environment is not None and environment.framework is not None
            else None
        ),
        baseline_status=FixScenarioStatus.SKIPPED,
        patched_status=FixScenarioStatus.SKIPPED,
        output=reason,
    )


def _materialize_artifact(artifact: FixTestArtifact) -> bool:
    try:
        if artifact.file_path.exists() and (
            artifact.file_path.read_text(encoding="utf-8") != artifact.content
        ):
            return False
        artifact.file_path.parent.mkdir(parents=True, exist_ok=True)
        artifact.file_path.write_text(artifact.content, encoding="utf-8")
        return True
    except OSError:
        return False


def _environment_for_path(
    path: Path,
    environments: dict[Path, FixProjectEnvironment],
) -> FixProjectEnvironment | None:
    matches = [
        environment
        for root, environment in environments.items()
        if path == root or root in path.parents
    ]
    if not matches:
        return None
    return max(matches, key=lambda environment: len(environment.project_root.parts))


def _scenario_frameworks(
    *,
    issue_id: UUID,
    scenarios: list[FixVerificationScenario],
    runnable_scenarios: dict[tuple[UUID, str], FixProjectEnvironment],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for scenario in scenarios:
        environment = runnable_scenarios[(issue_id, scenario.scenario_id)]
        if environment.framework is not None:
            result[scenario.scenario_id] = environment.framework.value
    return result


def _plan_scenarios(plan: FixIssuePlan) -> list[FixVerificationScenario]:
    return [*plan.exploit_scenarios, *plan.preserved_behavior_scenarios]


def _is_test_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name.startswith("test_")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
        or "tests" in {part.casefold() for part in path.parts}
        or "__tests__" in {part.casefold() for part in path.parts}
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
