"""Tests for dynamic semantic audit planning."""

from app.ai.semantic_audit_plan import build_semantic_audit_plan


def test_audit_plan_builds_queries_from_roadmap_hints() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "ai_verification_rules": [
                {
                    "rule_id": "RC-TEST-01",
                    "priority": "P0",
                    "skill_group": "Authentication",
                    "requirement": "Users must be authenticated with real tokens",
                    "verification_hint": (
                        "Confirm token creation, validation, expiry and rejection "
                        "of invalid credentials."
                    ),
                }
            ]
        },
        files_to_review=[],
        static_issues=[],
    )

    assert len(plan) == 1
    item = plan[0]
    assert item["reason"] == "roadmap_ai_verification"
    assert item["priority"] == "high"
    assert item["related_rule_ids"] == ["RC-TEST-01"]
    assert item["top_k"] == 5
    assert "Authentication" in str(item["query"])
    assert "RC-TEST-01" in str(item["query"])
    assert "hardcoded returns" in str(item["query"])


def test_audit_plan_groups_roadmap_rules_by_skill_group() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "ai_verification_rules": [
                {
                    "rule_id": "RC-AUTH-01",
                    "priority": "P0",
                    "skill_group": "Authentication",
                    "requirement": "Login verifies credentials",
                    "verification_hint": "Inspect login flow.",
                },
                {
                    "rule_id": "RC-AUTH-02",
                    "priority": "P1",
                    "skill_group": "Authentication",
                    "requirement": "Refresh token is validated",
                    "verification_hint": "Inspect refresh flow.",
                },
            ]
        },
        files_to_review=[],
        static_issues=[],
    )

    assert len(plan) == 1
    assert plan[0]["related_rule_ids"] == ["RC-AUTH-01", "RC-AUTH-02"]
    assert "Login verifies credentials" in str(plan[0]["query"])
    assert "Refresh token is validated" in str(plan[0]["query"])


def test_audit_plan_includes_general_file_and_static_followups() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context=None,
        files_to_review=[
            {
                "file_path": "app/payments/service.py",
                "priority": "high",
                "risk_area": "security",
            }
        ],
        static_issues=[
            {
                "file_path": "app/payments/service.py",
                "category": "security",
                "severity": "high",
            }
        ],
    )

    reasons = {item["reason"] for item in plan}
    assert reasons == {"high_risk_file", "static_finding_followup"}
    assert all("testingAI" not in str(item["query"]) for item in plan)
    assert any("app/payments/service.py" in str(item["query"]) for item in plan)
