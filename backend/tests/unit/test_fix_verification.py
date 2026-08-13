"""Behavior tests for dedicated post-patch semantic verifiers."""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.schemas.fix_job import (
    FixEvidenceReference,
    FixIssuePlan,
    FixIssuePlanStatus,
    FixIssueResult,
    FixIssueVerdict,
    FixScenarioKind,
    FixScenarioResult,
    FixScenarioStatus,
    FixVerificationScenario,
)
from app.services.fix_jobs.pipeline import verification
from app.services.fix_jobs.pipeline.contracts import (
    FixIssueSpec,
    FixVerificationResponse,
)

Verifier = Callable[[Path, FixIssueSpec, FixIssuePlan, int], FixIssueResult]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            'payload = jwt.decode(token, secret, algorithms=["HS256", "none"])\n',
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            "try:\n"
            "    payload = jwt.decode(token, secret, "
            "algorithms=[settings.ALGORITHM])\n"
            "except JWTError:\n"
            "    raise AuthenticationError\n",
            FixIssueVerdict.FIXED,
        ),
    ],
)
def test_jwt_verifier_requires_explicit_safe_allowlist(
    tmp_path: Path,
    source: str,
    expected: FixIssueVerdict,
) -> None:
    result = _run_verifier(
        tmp_path,
        source,
        "security.jwt_algorithm_allowlist",
        verification._verify_jwt,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "class ItemUpdate(BaseModel):\n    cost_price: float | None = None\n",
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            "class ItemUpdateUser(BaseModel):\n    name: str | None = None\n\n"
            "class ItemUpdateAdmin(BaseModel):\n"
            "    cost_price: float | None = None\n",
            FixIssueVerdict.FIXED,
        ),
    ],
)
def test_mass_assignment_verifier_checks_declared_sensitive_fields(
    tmp_path: Path,
    source: str,
    expected: FixIssueVerdict,
) -> None:
    result = _run_verifier(
        tmp_path,
        source,
        "security.mass_assignment",
        verification._verify_mass_assignment,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "async def adjust(item, delta):\n"
            "    new_quantity = item.quantity + delta\n"
            "    if new_quantity < 0:\n"
            "        raise ValueError\n"
            "    item.quantity = new_quantity\n",
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            "async def adjust(session, item, delta):\n"
            "    await session.execute(select(Item).with_for_update())\n"
            "    new_quantity = item.quantity + delta\n"
            "    if new_quantity < 0:\n"
            "        raise ValueError\n"
            "    item.quantity = new_quantity\n",
            FixIssueVerdict.FIXED,
        ),
    ],
)
def test_inventory_verifier_requires_bound_and_concurrency_guard(
    tmp_path: Path,
    source: str,
    expected: FixIssueVerdict,
) -> None:
    result = _run_verifier(
        tmp_path,
        source,
        "bug.inventory_invariant",
        verification._verify_inventory,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("response_field", "dependency", "expected"),
    [
        ("    cost_price: float\n", "get_current_user", FixIssueVerdict.UNRESOLVED),
        ("    name: str\n", "get_current_user", FixIssueVerdict.FIXED),
        ("    cost_price: float\n", "require_admin", FixIssueVerdict.FIXED),
    ],
)
def test_sensitive_response_verifier_follows_route_response_model(
    tmp_path: Path,
    response_field: str,
    dependency: str,
    expected: FixIssueVerdict,
) -> None:
    source = (
        "class ItemResponse(BaseModel):\n"
        f"{response_field}\n"
        "@router.get('/items', response_model=list[ItemResponse])\n"
        f"async def list_items(user=Depends({dependency})):\n"
        "    return []\n"
    )
    result = _run_verifier(
        tmp_path,
        source,
        "security.sensitive_response_exposure",
        verification._verify_sensitive_response,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "random.seed(time.time())\ntoken = random.choice(values)\n",
            FixIssueVerdict.UNRESOLVED,
        ),
        ("token = secrets.token_urlsafe(32)\n", FixIssueVerdict.FIXED),
    ],
)
def test_randomness_verifier_rejects_predictable_sources(
    tmp_path: Path,
    source: str,
    expected: FixIssueVerdict,
) -> None:
    result = _run_verifier(
        tmp_path,
        source,
        "security.insecure_randomness",
        verification._verify_randomness,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "parsed = urlparse(next)\n"
            "if parsed.scheme or parsed.netloc:\n    raise ValueError\n"
            "return RedirectResponse(next)\n",
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            "parsed = urlparse(target)\n"
            "if parsed.scheme or parsed.netloc or target.startswith('//'):\n"
            "    raise ValueError\n"
            "if '\\\\' in target:  # backslash redirect\n"
            "    raise ValueError\n"
            "return RedirectResponse(target)\n",
            FixIssueVerdict.FIXED,
        ),
    ],
)
def test_open_redirect_verifier_rejects_parser_only_patch(
    tmp_path: Path,
    source: str,
    expected: FixIssueVerdict,
) -> None:
    result = _run_verifier(
        tmp_path,
        source,
        "security.open_redirect",
        verification._verify_open_redirect,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("dependency", "expected"),
    [
        ("get_current_user", FixIssueVerdict.UNRESOLVED),
        ("require_admin", FixIssueVerdict.FIXED),
    ],
)
def test_rbac_verifier_requires_admin_on_sensitive_routes(
    tmp_path: Path,
    dependency: str,
    expected: FixIssueVerdict,
) -> None:
    source = (
        "@router.get('/profit')\n"
        f"async def profit_report(user=Depends({dependency})):\n"
        "    return {}\n"
    )
    result = _run_verifier(
        tmp_path,
        source,
        "security.role_authorization",
        verification._verify_rbac,
    )

    assert result.verdict == expected


@pytest.mark.parametrize(
    ("scenario_results", "semantic_verdict", "expected"),
    [
        (
            [
                ("exploit", "exploit", "failed", "passed"),
                ("positive", "preserved_behavior", "passed", "passed"),
            ],
            FixIssueVerdict.FIXED,
            FixIssueVerdict.FIXED,
        ),
        (
            [
                ("exploit", "exploit", "passed", "passed"),
                ("positive", "preserved_behavior", "passed", "passed"),
            ],
            FixIssueVerdict.FIXED,
            FixIssueVerdict.UNCERTAIN,
        ),
        (
            [
                ("exploit", "exploit", "failed", "passed"),
                ("positive", "preserved_behavior", "passed", "failed"),
            ],
            FixIssueVerdict.FIXED,
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            [
                ("exploit", "exploit", "failed", "passed"),
                ("positive", "preserved_behavior", "passed", "passed"),
                ("related:test.py", "related_test", "passed", "failed"),
            ],
            FixIssueVerdict.FIXED,
            FixIssueVerdict.UNRESOLVED,
        ),
        (
            [
                ("exploit", "exploit", "failed", "passed"),
                ("positive", "preserved_behavior", "passed", "passed"),
            ],
            FixIssueVerdict.UNRESOLVED,
            FixIssueVerdict.UNRESOLVED,
        ),
    ],
)
def test_scenario_contract_controls_final_verdict(
    scenario_results: list[tuple[str, str, str, str]],
    semantic_verdict: FixIssueVerdict,
    expected: FixIssueVerdict,
) -> None:
    issue_id = uuid4()
    result = FixIssueResult(
        issue_id=issue_id,
        verdict=semantic_verdict,
        summary="Semantic signal",
    )
    parsed_results = [
        FixScenarioResult(
            scenario_id=scenario_id,
            kind=FixScenarioKind(kind),
            framework="pytest",
            baseline_status=FixScenarioStatus(baseline),
            patched_status=FixScenarioStatus(patched),
        )
        for scenario_id, kind, baseline, patched in scenario_results
    ]

    verified = verification._enforce_scenario_contract(result, parsed_results)

    assert verified.verdict == expected
    assert verified.scenario_results == parsed_results


@pytest.mark.asyncio
async def test_llm_verifier_retries_group_then_each_missing_issue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_id = uuid4()
    second_id = uuid4()
    specs = [
        _build_spec(first_id, "custom.first", "first.py"),
        _build_spec(second_id, "custom.second", "second.py"),
    ]
    plans = [
        _build_plan(first_id, "custom.first", "first.py"),
        _build_plan(second_id, "custom.second", "second.py"),
    ]
    calls: list[list[UUID]] = []

    async def request_llm_verification(**kwargs: object) -> FixVerificationResponse:
        requested_specs = kwargs["specs"]
        assert isinstance(requested_specs, list)
        calls.append([spec.issue_id for spec in requested_specs])
        if len(requested_specs) > 1:
            return FixVerificationResponse(results=[])
        spec = requested_specs[0]
        return FixVerificationResponse(
            results=[
                FixIssueResult(
                    issue_id=spec.issue_id,
                    probe_id=spec.probe_id,
                    verdict=FixIssueVerdict.FIXED,
                    summary="verified",
                    verification_attempts=1,
                    evidence=[
                        FixEvidenceReference(
                            file_path=spec.file_path,
                            rationale="Post-patch property holds",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(
        verification,
        "_request_llm_verification",
        request_llm_verification,
    )

    results = await verification._verify_with_llm_contract(
        sandbox_path=tmp_path,
        specs=specs,
        plans=plans,
        attempt=1,
    )

    assert [result.issue_id for result in results] == [first_id, second_id]
    assert calls == [
        [first_id, second_id],
        [first_id, second_id],
        [first_id],
        [second_id],
    ]


@pytest.mark.asyncio
async def test_llm_verifier_contract_failure_becomes_uncertain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    spec = _build_spec(issue_id, "custom.issue", "app.py")
    plan = _build_plan(issue_id, "custom.issue", "app.py")

    async def request_unknown_result(**_kwargs: object) -> FixVerificationResponse:
        return FixVerificationResponse(
            results=[
                FixIssueResult(
                    issue_id=uuid4(),
                    verdict=FixIssueVerdict.FIXED,
                    summary="wrong issue",
                    verification_attempts=1,
                    evidence=[
                        FixEvidenceReference(
                            file_path="app.py",
                            rationale="Wrong issue",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(
        verification,
        "_request_llm_verification",
        request_unknown_result,
    )

    results = await verification._verify_with_llm_contract(
        sandbox_path=tmp_path,
        specs=[spec],
        plans=[plan],
        attempt=1,
    )

    assert len(results) == 1
    assert results[0].issue_id == issue_id
    assert results[0].verdict == FixIssueVerdict.UNCERTAIN
    assert "valid response" in results[0].summary


def test_fix_evidence_reference_normalizes_common_llm_aliases() -> None:
    evidence = FixEvidenceReference.model_validate(
        {
            "file": "backend/app/services/review_jobs/notifications.py",
            "explanation": "The unsafe deserialization path now rejects pickle data.",
        }
    )

    assert evidence.file_path == "backend/app/services/review_jobs/notifications.py"
    assert evidence.rationale.startswith("The unsafe deserialization")


@pytest.mark.asyncio
async def test_llm_verifier_retries_malformed_response_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issue_id = uuid4()
    spec = _build_spec(issue_id, "custom.issue", "app.py")
    plan = _build_plan(issue_id, "custom.issue", "app.py")
    calls: list[str | None] = []

    async def request_llm_verification(**kwargs: object) -> FixVerificationResponse:
        contract_error = kwargs.get("contract_error")
        assert contract_error is None or isinstance(contract_error, str)
        calls.append(contract_error)
        if len(calls) == 1:
            raise verification._VerificationContractError(
                "results.0.evidence.0.rationale is required"
            )
        return FixVerificationResponse(
            results=[
                FixIssueResult(
                    issue_id=issue_id,
                    probe_id=spec.probe_id,
                    verdict=FixIssueVerdict.FIXED,
                    summary="verified",
                    verification_attempts=1,
                    evidence=[
                        FixEvidenceReference(
                            file_path="app.py",
                            rationale="Post-patch property holds",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr(
        verification,
        "_request_llm_verification",
        request_llm_verification,
    )

    results = await verification._verify_with_llm_contract(
        sandbox_path=tmp_path,
        specs=[spec],
        plans=[plan],
        attempt=1,
    )

    assert results[0].verdict == FixIssueVerdict.FIXED
    assert calls == [None, "results.0.evidence.0.rationale is required"]


def test_verification_prompt_requires_exact_evidence_keys() -> None:
    prompt = verification._build_verification_prompt(
        [],
        attempt=2,
        retry=True,
        contract_error="rationale is required",
    )

    assert '"file_path"' in prompt
    assert '"rationale"' in prompt
    assert "rationale is required" in prompt
    assert '"verification_attempts": 2' in prompt
    assert "one cross-file patch" in prompt
    assert "pre_patch_source" in prompt
    assert "python-jose exposes `jose.jwt`" in prompt
    assert "lower and upper bounds" in prompt
    assert "predictable fallback secrets" in prompt


def _run_verifier(
    tmp_path: Path,
    source: str,
    probe_id: str,
    verifier: Verifier,
) -> FixIssueResult:
    file_path = "app.py"
    (tmp_path / file_path).write_text(source, encoding="utf-8")
    issue_id = uuid4()
    spec = _build_spec(issue_id, probe_id, file_path)
    plan = _build_plan(issue_id, probe_id, file_path)
    return verifier(tmp_path, spec, plan, 1)


def _build_spec(issue_id: UUID, probe_id: str, file_path: str) -> FixIssueSpec:
    return FixIssueSpec(
        issue_id=issue_id,
        probe_id=probe_id,
        file_path=file_path,
        line_start=1,
        line_end=1,
        severity="high",
        category="security",
        source="ai_review",
        title="Finding",
        description="Finding description",
        source_files={file_path: ""},
    )


def _build_plan(issue_id: UUID, probe_id: str, file_path: str) -> FixIssuePlan:
    return FixIssuePlan(
        issue_id=issue_id,
        probe_id=probe_id,
        root_cause="Root cause",
        safety_property="Safety property",
        editable_files=[file_path],
        context_files=[file_path],
        affected_contracts=["behavior contract"],
        exploit_scenarios=[
            FixVerificationScenario(
                scenario_id="exploit",
                kind=FixScenarioKind.EXPLOIT,
                description="Unsafe behavior is reproduced.",
                related_files=[file_path],
            )
        ],
        preserved_behavior_scenarios=[
            FixVerificationScenario(
                scenario_id="positive",
                kind=FixScenarioKind.PRESERVED_BEHAVIOR,
                description="Safe behavior remains available.",
                related_files=[file_path],
            )
        ],
        acceptance_checks=["Check"],
        forbidden_shortcuts=[],
        status=FixIssuePlanStatus.PLANNED,
    )
