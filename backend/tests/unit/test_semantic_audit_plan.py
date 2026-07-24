"""Tests for typed semantic audit planning."""

from app.ai.probe.contracts import ProbeDefinition, ProbeLane
from app.ai.probe.plan import BASELINE_PROBES, build_semantic_audit_plan


def test_baseline_probes_use_short_focused_queries_and_dynamic_top_k() -> None:
    assert BASELINE_PROBES
    assert all(isinstance(probe, ProbeDefinition) for probe in BASELINE_PROBES)
    assert all(probe.lane is ProbeLane.DEFECT for probe in BASELINE_PROBES)
    assert all(
        len(query.split()) <= 64
        for probe in BASELINE_PROBES
        for query in probe.retrieval_queries
    )

    probes_by_id = {probe.probe_id: probe for probe in BASELINE_PROBES}
    assert probes_by_id["security.object_authorization"].top_k == 6
    assert probes_by_id["bug.async_concurrency"].top_k == 5
    assert probes_by_id["performance.n_plus_one"].top_k == 5
    assert probes_by_id["maintainability.resource_lifecycle"].top_k == 4
    assert probes_by_id["style.boundary_contracts"].top_k == 3


def test_roadmap_rules_are_grouped_into_typed_roadmap_probes() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "review_rules": [
                {
                    "rule_id": "RC-AUTH-01",
                    "priority": "P0",
                    "skill_group": "Authentication",
                    "review_category": "security",
                    "check_type": "required_code_pattern",
                    "requirement": "Login verifies credentials",
                    "verification_hint": "Inspect login token validation.",
                },
                {
                    "rule_id": "RC-AUTH-02",
                    "priority": "P1",
                    "skill_group": "Authentication",
                    "review_category": "security",
                    "check_type": "required_code_pattern",
                    "requirement": "Refresh tokens expire",
                    "verification_hint": "Inspect refresh expiry.",
                },
            ]
        },
        files_to_review=[],
        static_issues=[],
    )

    roadmap = [probe for probe in plan if probe.lane is ProbeLane.ROADMAP]
    assert len(roadmap) == 1
    assert roadmap[0].related_rule_ids == ("RC-AUTH-01", "RC-AUTH-02")
    assert roadmap[0].top_k == 1
    assert roadmap[0].category == "security"
    assert "roadmap requirements" in roadmap[0].judge_question


def test_large_roadmap_catalog_does_not_drop_rules() -> None:
    categories = ["security", "performance", "structure", "ai"]
    rules = [
        {
            "rule_id": f"RC-TEST-{index:02d}",
            "priority": "P1",
            "skill_group": f"Skill {index % 4}",
            "review_category": categories[index % len(categories)],
            "check_type": "required_code_pattern",
            "needs_ai_verification": index % 5 == 0,
            "requirement": f"Requirement {index}",
            "verification_hint": f"Inspect behavior {index}.",
        }
        for index in range(1, 80)
    ]

    plan = build_semantic_audit_plan(
        roadmap_context={"review_rules": rules},
        files_to_review=[],
        static_issues=[],
    )
    planned_rule_ids = {
        rule_id
        for probe in plan
        if probe.lane is ProbeLane.ROADMAP
        for rule_id in probe.related_rule_ids
    }
    assert planned_rule_ids == {str(rule["rule_id"]) for rule in rules}
    roadmap_probes = [probe for probe in plan if probe.lane is ProbeLane.ROADMAP]
    assert len(roadmap_probes) <= 32
    assert all(len(probe.related_rule_ids) <= 3 for probe in roadmap_probes)


def test_high_risk_files_create_hard_scoped_coverage_probes() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context=None,
        files_to_review=[
            {
                "file_path": "app/auth.py",
                "priority": "high",
                "risk_area": "security",
            },
            {
                "file_path": "app/readme_helper.py",
                "priority": "low",
                "risk_area": "general",
            },
        ],
        static_issues=[],
    )

    coverage = [probe for probe in plan if probe.lane is ProbeLane.COVERAGE]
    assert len(coverage) == 1
    assert coverage[0].file_scope == "app/auth.py"
    assert coverage[0].top_k == 6


def test_static_findings_do_not_change_ai_probe_plan() -> None:
    without_static = build_semantic_audit_plan(
        roadmap_context=None,
        files_to_review=[],
        static_issues=[],
    )
    with_static = build_semantic_audit_plan(
        roadmap_context=None,
        files_to_review=[],
        static_issues=[
            {
                "file_path": "app/auth.py",
                "category": "security",
                "severity": "critical",
            }
        ],
    )

    assert with_static == without_static
