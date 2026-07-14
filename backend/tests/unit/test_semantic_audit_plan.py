"""Tests for dynamic semantic audit planning."""

from app.ai.semantic_audit_plan import build_semantic_audit_plan


def test_audit_plan_builds_queries_from_roadmap_hints() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "review_rules": [
                {
                    "rule_id": "RC-TEST-01",
                    "priority": "P0",
                    "skill_group": "Authentication",
                    "review_category": "security",
                    "check_type": "required_code_pattern",
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

    item = next(
        item
        for item in plan
        if item["reason"] == "category_probe"
        and item.get("probe_kind") == "roadmap"
        and item["category"] == "security"
    )
    assert item["reason"] == "category_probe"
    assert item["audit_plan_item_id"].startswith("category_probe:security.")
    assert item["priority"] == "high"
    assert item["review_category"] == "security"
    assert item["source_kinds"] == ["roadmap"]
    assert item["related_rule_ids"] == ["RC-TEST-01"]
    assert item["top_k"] == 5
    assert item["probe_kind"] == "roadmap"
    assert "Authentication" in str(item["query"])
    assert "RC-TEST-01" in str(item["query"])
    assert "Users must be authenticated" in str(item["query"])
    assert "contradiction checks" in str(item["query"])

    baseline_item = next(
        item
        for item in plan
        if item["audit_plan_item_id"] == "category_probe:security.sql_nosql_injection"
    )
    assert baseline_item["probe_kind"] == "baseline"
    assert baseline_item["source_kinds"] == ["baseline"]
    assert "SQL" in str(baseline_item["query"])


def test_audit_plan_groups_roadmap_rules_by_category() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "review_rules": [
                {
                    "rule_id": "RC-AUTH-01",
                    "priority": "P0",
                    "skill_group": "Authentication",
                    "review_category": "security",
                    "requirement": "Login verifies credentials",
                    "verification_hint": "Inspect login flow.",
                },
                {
                    "rule_id": "RC-AUTH-02",
                    "priority": "P1",
                    "skill_group": "Authentication",
                    "review_category": "security",
                    "requirement": "Refresh token is validated",
                    "verification_hint": "Inspect refresh flow.",
                },
                {
                    "rule_id": "RC-CACHE-01",
                    "priority": "P1",
                    "skill_group": "Caching",
                    "review_category": "performance",
                    "requirement": "Cache entries expire",
                    "verification_hint": "Inspect cache TTL flow.",
                },
            ]
        },
        files_to_review=[],
        static_issues=[],
    )

    probe_items = [item for item in plan if item["reason"] == "category_probe"]
    security_item = next(
        item
        for item in probe_items
        if item["review_category"] == "security" and item.get("probe_kind") == "roadmap"
    )
    assert security_item["related_rule_ids"] == ["RC-AUTH-01", "RC-AUTH-02"]
    assert security_item["source_kinds"] == ["roadmap"]
    assert "Authentication" in str(security_item["query"])
    assert "Login verifies credentials" in str(security_item["query"])
    assert "Refresh token is validated" in str(security_item["query"])
    performance_item = next(
        item
        for item in probe_items
        if item["review_category"] == "performance"
        and item.get("probe_kind") == "roadmap"
    )
    assert performance_item["audit_plan_item_id"].startswith(
        "category_probe:performance."
    )
    assert performance_item["related_rule_ids"] == ["RC-CACHE-01"]
    assert "Cache entries expire" in str(performance_item["query"])


def test_audit_plan_reviews_rules_without_ai_verification_flag() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context={
            "review_rules": [
                {
                    "rule_id": "RC-STRUCT-01",
                    "priority": "P2",
                    "skill_group": "Project Structure",
                    "review_category": "structure",
                    "check_type": "required_file",
                    "needs_ai_verification": False,
                    "requirement": "Alembic env.py must exist",
                    "verification_hint": "Inspect migration setup.",
                }
            ],
            "ai_verification_rules": [],
        },
        files_to_review=[],
        static_issues=[],
    )

    structure_item = next(
        item
        for item in plan
        if item["reason"] == "category_probe" and item["category"] == "structure"
    )
    assert structure_item["related_rule_ids"] == ["RC-STRUCT-01"]
    assert structure_item["source_kinds"] == ["roadmap"]
    assert "Alembic env.py must exist" in str(structure_item["query"])


def test_audit_plan_covers_large_roadmap_rule_set_without_dropping_categories() -> None:
    categories = [
        "security",
        "performance",
        "structure",
        "ai",
        "realtime",
        "requirement",
    ]
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
        for item in plan
        for rule_id in item.get("related_rule_ids", [])
        if isinstance(rule_id, str)
    }
    assert planned_rule_ids == {str(rule["rule_id"]) for rule in rules}
    assert {
        item["review_category"]
        for item in plan
        if item["reason"] == "category_probe" and item.get("related_rule_ids")
    } == set(categories)


def test_audit_plan_includes_category_reviews_outside_roadmap() -> None:
    plan = build_semantic_audit_plan(
        roadmap_context=None,
        files_to_review=[
            {
                "file_path": "app/auth/routes.py",
                "priority": "high",
                "risk_area": "security",
            },
            {
                "file_path": "app/products/repository.py",
                "priority": "medium",
                "risk_area": "performance",
            },
        ],
        static_issues=[],
    )

    probe_items = [item for item in plan if item["reason"] == "category_probe"]
    categories = {item["category"] for item in probe_items}
    assert categories == {
        "security",
        "bug",
        "performance",
        "maintainability",
        "style",
    }
    combined_queries = " ".join(str(item["query"]) for item in probe_items)
    assert "SQL" in combined_queries
    assert "database queries" in combined_queries
    assert "dead code" in combined_queries
    assert "complex functions" in combined_queries
    assert "app/auth/routes.py" in combined_queries
    assert all(item["source_kinds"] == ["baseline"] for item in probe_items)
    assert {
        item["audit_plan_item_id"]
        for item in probe_items
        if item["category"] == "security"
    } >= {
        "category_probe:security.sql_nosql_injection",
        "category_probe:security.jwt_session_auth",
    }


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
    assert reasons == {
        "category_probe",
        "high_risk_file",
        "static_finding_followup",
    }
    assert all("testingAI" not in str(item["query"]) for item in plan)
    assert any("app/payments/service.py" in str(item["query"]) for item in plan)
