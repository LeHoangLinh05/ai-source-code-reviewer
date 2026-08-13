"""Behavior tests for cross-file fix planning and generation."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.schemas.fix_job import (
    FixIssuePlan,
    FixIssuePlanStatus,
    FixScenarioKind,
    FixVerificationScenario,
)
from app.services.fix_jobs.pipeline import generation, planning
from app.services.fix_jobs.pipeline.contracts import (
    FixGenerationDisposition,
    FixGenerationDispositionStatus,
    FixGenerationResponse,
    FixIssueSpec,
    FixPlanningResponse,
    FixUpdatedFile,
)
from app.services.fix_jobs.pipeline.errors import FixPipelineError


@pytest.mark.asyncio
async def test_generation_processes_more_than_five_planned_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plans: list[FixIssuePlan] = []
    specs: list[FixIssueSpec] = []
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for index in range(6):
        issue_id = uuid4()
        relative_path = f"src/file_{index}.py"
        (tmp_path / relative_path).write_text("value = 0\n", encoding="utf-8")
        plans.append(_build_plan(issue_id, relative_path))
        specs.append(_build_spec(issue_id, relative_path))

    requested_components: list[list[str]] = []

    async def request_generation_with_contract(
        *,
        sandbox_path: Path,
        specs: list[FixIssueSpec],
        plans: list[FixIssuePlan],
        repair_context: dict[str, object] | None,
    ) -> FixGenerationResponse:
        del sandbox_path, specs, repair_context
        requested_components.append([str(plan.issue_id) for plan in plans])
        plan = plans[0]
        return FixGenerationResponse(
            files=[
                FixUpdatedFile(
                    path=plan.editable_files[0],
                    updated_content="value = 1\n",
                )
            ],
            dispositions=[
                FixGenerationDisposition(
                    issue_id=plan.issue_id,
                    status=FixGenerationDispositionStatus.CHANGED,
                    summary="updated",
                )
            ],
        )

    monkeypatch.setattr(
        generation,
        "_request_generation_with_contract",
        request_generation_with_contract,
    )

    await generation._generate_plan_components(
        sandbox_path=tmp_path,
        specs=specs,
        plans=plans,
        repair_context=None,
    )

    assert len(requested_components) == 6
    assert all(
        (tmp_path / f"src/file_{index}.py").read_text(encoding="utf-8") == "value = 1\n"
        for index in range(6)
    )


def test_generation_rejects_file_outside_issue_plan(tmp_path: Path) -> None:
    issue_id = uuid4()
    (tmp_path / "allowed.py").write_text("safe = True\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("safe = True\n", encoding="utf-8")
    response = FixGenerationResponse(
        files=[FixUpdatedFile(path="outside.py", updated_content="safe = False\n")],
        dispositions=[
            FixGenerationDisposition(
                issue_id=issue_id,
                status=FixGenerationDispositionStatus.CHANGED,
                summary="changed",
            )
        ],
    )

    with pytest.raises(FixPipelineError, match="unplanned file"):
        generation._apply_generation_response(
            sandbox_path=tmp_path,
            plans=[_build_plan(issue_id, "allowed.py")],
            response=response,
        )


def test_generation_never_edits_a_planned_test_file(tmp_path: Path) -> None:
    issue_id = uuid4()
    test_path = "tests/test_app.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / test_path).write_text("def test_app():\n    assert True\n")
    response = FixGenerationResponse(
        files=[
            FixUpdatedFile(
                path=test_path,
                updated_content="def test_app():\n    assert False\n",
            )
        ],
        dispositions=[
            FixGenerationDisposition(
                issue_id=issue_id,
                status=FixGenerationDispositionStatus.CHANGED,
                summary="Changed a test",
            )
        ],
    )

    with pytest.raises(FixPipelineError, match="create or edit a test"):
        generation._apply_generation_response(
            sandbox_path=tmp_path,
            plans=[_build_plan(issue_id, test_path)],
            response=response,
        )


def test_plan_paths_allow_context_owned_by_another_selected_issue() -> None:
    first_id = uuid4()
    second_id = uuid4()
    specs = [
        _build_spec(first_id, "first.py"),
        _build_spec(second_id, "shared.py"),
    ]
    plan = _build_plan(first_id, "shared.py")

    generation._validate_plan_paths(specs=specs, plans=[plan])


def test_plan_paths_reject_files_outside_all_selected_contexts() -> None:
    issue_id = uuid4()
    specs = [_build_spec(issue_id, "allowed.py")]
    plan = _build_plan(issue_id, "outside.py")

    with pytest.raises(FixPipelineError, match="outside selected issue context"):
        generation._validate_plan_paths(specs=specs, plans=[plan])


def test_plan_paths_reject_scenario_files_outside_selected_contexts() -> None:
    issue_id = uuid4()
    specs = [_build_spec(issue_id, "allowed.py")]
    plan = _build_plan(issue_id, "allowed.py")
    plan.exploit_scenarios[0].related_files = ["outside.py"]

    with pytest.raises(FixPipelineError, match="outside selected issue context"):
        generation._validate_plan_paths(specs=specs, plans=[plan])


def test_planner_contract_rejects_duplicate_scenario_ids() -> None:
    plan = _build_plan(uuid4(), "allowed.py")
    plan.preserved_behavior_scenarios[0].scenario_id = plan.exploit_scenarios[
        0
    ].scenario_id

    assert planning._plan_satisfies_contract(plan) is False


def test_planner_contract_rejects_planned_fix_when_context_is_incomplete() -> None:
    plan = _build_plan(uuid4(), "allowed.py")

    assert (
        planning._plan_satisfies_contract(
            plan,
            context_truncated=True,
        )
        is False
    )


def test_planner_contract_rejects_editing_agent_instructions() -> None:
    plan = _build_plan(uuid4(), "AGENTS.md")

    assert planning._plan_satisfies_contract(plan) is False


def test_missing_primary_file_marks_issue_context_incomplete(tmp_path: Path) -> None:
    issue = _build_issue(file_path="src/missing.py")

    spec = planning.build_fix_issue_spec(sandbox_path=tmp_path, issue=issue)

    assert spec.context_truncated is True
    assert spec.source_files == {}


@pytest.mark.asyncio
async def test_generation_contract_retries_invalid_response_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    plan = _build_plan(issue_id, "allowed.py")
    spec = _build_spec(issue_id, "allowed.py")
    retry_reasons: list[str | None] = []

    async def request_generation(
        *,
        sandbox_path: Path,
        specs: list[FixIssueSpec],
        plans: list[FixIssuePlan],
        repair_context: dict[str, object] | None,
        retry_reason: str | None,
    ) -> FixGenerationResponse:
        del sandbox_path, specs, plans, repair_context
        retry_reasons.append(retry_reason)
        if retry_reason is None:
            raise generation._GenerationResponseContractError(
                "files must be a JSON array"
            )
        return FixGenerationResponse(
            files=[],
            dispositions=[
                FixGenerationDisposition(
                    issue_id=issue_id,
                    status=FixGenerationDispositionStatus.CHANGED,
                    summary="Applied the planned fix.",
                )
            ],
        )

    monkeypatch.setattr(generation, "_request_generation", request_generation)

    response = await generation._request_generation_with_contract(
        sandbox_path=tmp_path,
        specs=[spec],
        plans=[plan],
        repair_context=None,
    )

    assert response.dispositions[0].issue_id == issue_id
    assert retry_reasons == [None, "files must be a JSON array"]


@pytest.mark.asyncio
async def test_generation_contract_wraps_repeated_schema_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    plan = _build_plan(issue_id, "allowed.py")
    spec = _build_spec(issue_id, "allowed.py")
    retry_reasons: list[str | None] = []

    async def request_generation(
        *,
        sandbox_path: Path,
        specs: list[FixIssueSpec],
        plans: list[FixIssuePlan],
        repair_context: dict[str, object] | None,
        retry_reason: str | None,
    ) -> FixGenerationResponse:
        del sandbox_path, specs, plans, repair_context
        retry_reasons.append(retry_reason)
        raise generation._GenerationResponseContractError(
            "dispositions.summary is required"
        )

    monkeypatch.setattr(generation, "_request_generation", request_generation)

    with pytest.raises(FixPipelineError, match="failed after 2 attempts"):
        await generation._request_generation_with_contract(
            sandbox_path=tmp_path,
            specs=[spec],
            plans=[plan],
            repair_context=None,
        )

    assert retry_reasons == [None, "dispositions.summary is required"]


@pytest.mark.asyncio
async def test_request_generation_wraps_logged_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ai.llm import config as llm_config

    issue_id = uuid4()
    file_path = "allowed.py"
    (tmp_path / file_path).write_text("safe = True\n", encoding="utf-8")

    class FakeLLM:
        async def ainvoke(self, messages: object) -> str:
            del messages
            return (
                '{"files":{"allowed.py":"safe = false\\n"},'
                '"dispositions":[{"issue_id":"'
                f"{issue_id}"
                '","status":"changed"}]}'
            )

    async def run_with_configured_llm(
        call: Callable[[FakeLLM], Awaitable[object]],
    ) -> object:
        return await call(FakeLLM())

    monkeypatch.setattr(
        llm_config,
        "run_with_configured_llm",
        run_with_configured_llm,
    )

    with pytest.raises(
        generation._GenerationResponseContractError,
        match="response schema validation failed",
    ) as raised_error:
        await generation._request_generation(
            sandbox_path=tmp_path,
            specs=[_build_spec(issue_id, file_path)],
            plans=[_build_plan(issue_id, file_path)],
            repair_context=None,
            retry_reason=None,
        )

    assert '"field": "files"' in str(raised_error.value)
    assert '"field": "dispositions.0.summary"' in str(raised_error.value)


def test_generation_prompt_requires_array_shape_and_summary(tmp_path: Path) -> None:
    issue_id = uuid4()
    file_path = "allowed.py"
    (tmp_path / file_path).write_text("safe = True\n", encoding="utf-8")

    prompt = generation._build_generation_prompt(
        sandbox_path=tmp_path,
        specs=[_build_spec(issue_id, file_path)],
        plans=[_build_plan(issue_id, file_path)],
        repair_context=None,
        retry_reason="files must be a JSON array",
    )

    assert '"files":[{' in prompt
    assert "Never encode files as an object keyed by path" in prompt
    assert "Every disposition must include a non-empty summary" in prompt
    assert "Previous response contract error: files must be a JSON array" in prompt


def test_issue_context_includes_cross_file_supporting_evidence(tmp_path: Path) -> None:
    api_path = tmp_path / "app/api/items.py"
    schema_path = tmp_path / "app/schemas/item.py"
    api_path.parent.mkdir(parents=True)
    schema_path.parent.mkdir(parents=True)
    api_path.write_text("from app.schemas.item import ItemUpdate\n", encoding="utf-8")
    schema_path.write_text("class ItemUpdate:\n    pass\n", encoding="utf-8")
    issue = _build_issue(
        file_path="app/api/items.py",
        raw_output={
            "probe_review": {
                "probe_id": "security.mass_assignment",
                "supporting_evidence": [
                    {
                        "file_path": "app/schemas/item.py",
                        "line_start": 1,
                        "line_end": 2,
                    }
                ],
            }
        },
    )

    spec = planning.build_fix_issue_spec(sandbox_path=tmp_path, issue=issue)

    assert set(spec.source_files) >= {
        "app/api/items.py",
        "app/schemas/item.py",
    }
    assert spec.probe_id == "security.mass_assignment"


def test_issue_context_follows_typescript_consumers_and_tests(tmp_path: Path) -> None:
    api_path = tmp_path / "src/api/items.ts"
    consumer_path = tmp_path / "src/components/item-list.tsx"
    test_path = tmp_path / "src/components/item-list.test.tsx"
    api_path.parent.mkdir(parents=True)
    consumer_path.parent.mkdir(parents=True)
    api_path.write_text(
        "export interface ItemResponse { name: string }\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        "import type { ItemResponse } from '../api/items';\n"
        "export function ItemList(item: ItemResponse) { return item.name; }\n",
        encoding="utf-8",
    )
    test_path.write_text(
        "import { ItemList } from './item-list';\n",
        encoding="utf-8",
    )
    issue = _build_issue(file_path="src/api/items.ts")

    spec = planning.build_fix_issue_spec(sandbox_path=tmp_path, issue=issue)

    assert "src/components/item-list.tsx" in spec.source_files
    assert "src/components/item-list.test.tsx" in spec.source_files


def test_issue_context_includes_project_manifests_and_config(tmp_path: Path) -> None:
    source_path = tmp_path / "backend/app/auth.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("from jose import jwt\n", encoding="utf-8")
    requirements_path = tmp_path / "backend/requirements.txt"
    requirements_path.write_text(
        "python-jose[cryptography]==3.3.0\n",
        encoding="utf-8",
    )
    environment_path = tmp_path / "backend/.env.example"
    environment_path.write_text("JWT_SECRET=\n", encoding="utf-8")
    instructions_path = tmp_path / "AGENTS.md"
    instructions_path.write_text("Use python-jose.\n", encoding="utf-8")

    spec = planning.build_fix_issue_spec(
        sandbox_path=tmp_path,
        issue=_build_issue(file_path="backend/app/auth.py"),
    )
    plan = _build_plan(spec.issue_id, "backend/app/auth.py")
    planning._include_project_support_context(specs=[spec], plans=[plan])

    assert "backend/requirements.txt" in spec.source_files
    assert "backend/.env.example" in spec.source_files
    assert "AGENTS.md" in spec.source_files
    assert "backend/requirements.txt" in plan.context_files
    assert "backend/.env.example" in plan.context_files
    assert "AGENTS.md" in plan.context_files


@pytest.mark.asyncio
async def test_planner_retries_group_then_individual_missing_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_spec = _build_spec(uuid4(), "first.py")
    second_spec = _build_spec(uuid4(), "second.py")
    calls: list[list[str]] = []

    async def request_plans(
        specs: list[FixIssueSpec],
        *,
        retry_reason: str | None,
    ) -> FixPlanningResponse:
        del retry_reason
        calls.append([str(spec.issue_id) for spec in specs])
        if len(specs) > 1:
            return FixPlanningResponse(plans=[])
        spec = specs[0]
        return FixPlanningResponse(plans=[_build_plan(spec.issue_id, spec.file_path)])

    monkeypatch.setattr(planning, "_request_plans", request_plans)

    plans = await planning._request_plans_with_contract([first_spec, second_spec])

    assert [plan.issue_id for plan in plans] == [
        first_spec.issue_id,
        second_spec.issue_id,
    ]
    assert calls == [
        [str(first_spec.issue_id), str(second_spec.issue_id)],
        [str(first_spec.issue_id), str(second_spec.issue_id)],
        [str(first_spec.issue_id)],
        [str(second_spec.issue_id)],
    ]


def test_planner_normalizes_structured_affected_contracts() -> None:
    issue_id = uuid4()
    payload = _build_plan(issue_id, "items.py").model_dump(mode="json")
    payload["affected_contracts"] = [
        {
            "contract_id": "item-create",
            "path": "/api/items",
            "method": "POST",
        }
    ]

    response = FixPlanningResponse.model_validate({"plans": [payload]})

    assert response.plans[0].affected_contracts == ["item-create [POST /api/items]"]


@pytest.mark.asyncio
async def test_planner_retries_invalid_response_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _build_spec(uuid4(), "items.py")
    retry_reasons: list[str | None] = []

    async def request_plans(
        specs: list[FixIssueSpec],
        *,
        retry_reason: str | None,
    ) -> FixPlanningResponse:
        retry_reasons.append(retry_reason)
        if retry_reason is None:
            raise planning._PlanningResponseContractError(
                "affected_contracts must contain strings"
            )
        return FixPlanningResponse(
            plans=[_build_plan(specs[0].issue_id, specs[0].file_path)]
        )

    monkeypatch.setattr(planning, "_request_plans", request_plans)

    plans = await planning._request_plans_with_contract([spec])

    assert plans[0].issue_id == spec.issue_id
    assert retry_reasons == [None, "affected_contracts must contain strings"]


def test_planning_prompt_requires_string_contracts() -> None:
    spec = _build_spec(uuid4(), "items.py")

    prompt = planning._build_planning_prompt([spec], retry_reason=None)

    assert "affected_contracts must be a JSON array of non-empty strings" in prompt
    assert '["POST /api/items request body"]' in prompt
    assert "dependency manifests and configuration templates" in prompt
    assert "from jose import jwt" in prompt
    assert "explicit lower and upper constraints" in prompt
    assert "predictable fallback" in prompt


def test_generation_prompt_requires_cross_file_logic_checks(tmp_path: Path) -> None:
    issue_id = uuid4()
    file_path = "allowed.py"
    (tmp_path / file_path).write_text("safe = True\n", encoding="utf-8")

    prompt = generation._build_generation_prompt(
        sandbox_path=tmp_path,
        specs=[_build_spec(issue_id, file_path)],
        plans=[_build_plan(issue_id, file_path)],
        repair_context=None,
        retry_reason=None,
    )

    assert "one cross-file change" in prompt
    assert "dependency manifest" in prompt
    assert "from jose import jwt" in prompt
    assert "lower and upper bounds" in prompt
    assert "predictable fallback secrets" in prompt


def _build_plan(issue_id: UUID, file_path: str) -> FixIssuePlan:
    return FixIssuePlan(
        issue_id=issue_id,
        probe_id="security.mass_assignment",
        root_cause="Unsafe request field",
        safety_property="Sensitive fields are not user assignable",
        editable_files=[file_path],
        context_files=[file_path],
        affected_contracts=["item write contract"],
        exploit_scenarios=[
            FixVerificationScenario(
                scenario_id="reject-sensitive-field",
                kind=FixScenarioKind.EXPLOIT,
                description="Sensitive fields cannot be assigned by public input.",
                related_files=[file_path],
            )
        ],
        preserved_behavior_scenarios=[
            FixVerificationScenario(
                scenario_id="allow-normal-update",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                description="Normal public fields remain updateable.",
                related_files=[file_path],
            )
        ],
        acceptance_checks=["Sensitive fields are rejected"],
        forbidden_shortcuts=["Do not hide the field only in the UI"],
        status=FixIssuePlanStatus.PLANNED,
    )


def _build_spec(issue_id: UUID, file_path: str) -> FixIssueSpec:
    return FixIssueSpec(
        issue_id=issue_id,
        probe_id="security.mass_assignment",
        file_path=file_path,
        line_start=1,
        line_end=1,
        severity="high",
        category="security",
        source="ai_review",
        title="Finding",
        description="Finding description",
        source_files={file_path: "value = 0\n"},
    )


def _build_issue(
    *,
    file_path: str,
    raw_output: dict[str, object] | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        id=uuid4(),
        job_id=uuid4(),
        file_path=file_path,
        line_start=1,
        line_end=1,
        severity=IssueSeverity.HIGH,
        category=IssueCategory.SECURITY,
        title="Finding",
        description="Finding description",
        suggestion=None,
        source=IssueSource.AI_REVIEW,
        confidence=0.9,
        raw_output=raw_output,
    )
