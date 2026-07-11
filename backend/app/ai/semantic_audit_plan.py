"""Build dynamic semantic audit guidance for the Review Agent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

MAX_AUDIT_PLAN_ITEMS = 16
MAX_ROADMAP_AUDIT_ITEMS = 8
MAX_FILE_AUDIT_ITEMS = 4
MAX_STATIC_AUDIT_ITEMS = 2

HIGH_PRIORITY_VALUES = {"p0", "critical", "high"}


def build_semantic_audit_plan(
    *,
    roadmap_context: dict[str, object] | None,
    files_to_review: Sequence[dict[str, object]],
    static_issues: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Return dynamic semantic-search seeds without hard-coding benchmark bugs."""

    plan: list[dict[str, object]] = []
    seen_queries: set[str] = set()

    for item in _roadmap_audit_items(roadmap_context):
        _append_unique(plan, seen_queries, item)

    for item in _file_audit_items(files_to_review):
        _append_unique(plan, seen_queries, item)

    for item in _static_audit_items(static_issues):
        _append_unique(plan, seen_queries, item)

    return plan[:MAX_AUDIT_PLAN_ITEMS]


def _roadmap_audit_items(
    roadmap_context: dict[str, object] | None,
) -> list[dict[str, object]]:
    if roadmap_context is None:
        return []

    raw_rules = roadmap_context.get("ai_verification_rules", [])
    if not isinstance(raw_rules, list):
        return []

    grouped_rules: dict[str, list[dict[str, object]]] = {}
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue

        rule_id = _string(raw_rule.get("rule_id"))
        requirement = _string(raw_rule.get("requirement"))
        verification_hint = _string(raw_rule.get("verification_hint"))
        skill_group = _string(raw_rule.get("skill_group"))
        priority = _string(raw_rule.get("priority")) or "P1"
        if not rule_id or not (requirement or verification_hint):
            continue

        group_key = skill_group or "Roadmap Requirements"
        grouped_rules.setdefault(group_key, []).append(
            {
                "rule_id": rule_id,
                "requirement": requirement,
                "verification_hint": verification_hint,
                "priority": _priority(priority),
            }
        )

    items: list[dict[str, object]] = []
    for skill_group, rules in grouped_rules.items():
        sorted_rules = sorted(rules, key=lambda rule: str(rule["rule_id"]))
        query_parts = [
            "Verify whether the repository implements these related roadmap "
            "requirements end-to-end",
        ]
        if skill_group:
            query_parts.append(f"skill group: {skill_group}")
        for rule in sorted_rules:
            rule_text = f"rule {rule['rule_id']}"
            if rule["requirement"]:
                rule_text += f"; requirement: {rule['requirement']}"
            if rule["verification_hint"]:
                rule_text += f"; verification hint: {rule['verification_hint']}"
            query_parts.append(rule_text)
        query_parts.append(
            "look for real control/data flow, missing integration, stubs, mocks, "
            "hardcoded returns, fake pass-through logic, and incomplete behavior"
        )

        items.append(
            {
                "query": "; ".join(query_parts),
                "reason": "roadmap_ai_verification",
                "priority": _highest_priority(sorted_rules),
                "related_rule_ids": [str(rule["rule_id"]) for rule in sorted_rules],
                "top_k": 5,
            }
        )

    return sorted(
        items,
        key=lambda item: (
            _priority_rank(_string(item.get("priority"))),
            _first_string(item.get("related_rule_ids")),
        ),
    )[:MAX_ROADMAP_AUDIT_ITEMS]


def _first_string(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    return str(value[0])


def _file_audit_items(
    files_to_review: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for file_info in files_to_review:
        file_path = _string(file_info.get("file_path"))
        priority = _string(file_info.get("priority")) or "low"
        risk_area = _string(file_info.get("risk_area")) or "general"
        if not file_path or priority not in {"high", "medium"}:
            continue

        items.append(
            {
                "query": (
                    f"Review {file_path} for real end-to-end behavior, hidden "
                    f"logic bugs, incomplete branches, insecure defaults, and "
                    f"stubbed or hardcoded implementation paths"
                ),
                "reason": "high_risk_file",
                "priority": priority,
                "file_path": file_path,
                "risk_area": risk_area,
                "top_k": 3,
            }
        )

    return sorted(
        items,
        key=lambda item: (
            _priority_rank(_string(item.get("priority"))),
            str(item.get("file_path")),
        ),
    )[:MAX_FILE_AUDIT_ITEMS]


def _static_audit_items(
    static_issues: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: Counter[tuple[str, str]] = Counter()
    paths_by_group: dict[tuple[str, str], set[str]] = {}
    for issue in static_issues:
        category = _string(issue.get("category")) or "general"
        severity = _string(issue.get("severity")) or "unknown"
        key = (category, severity)
        grouped[key] += 1
        file_path = _string(issue.get("file_path"))
        if file_path:
            paths_by_group.setdefault(key, set()).add(file_path)

    items: list[dict[str, object]] = []
    for (category, severity), count in grouped.most_common(MAX_STATIC_AUDIT_ITEMS):
        paths = sorted(paths_by_group.get((category, severity), set()))[:5]
        path_text = ", ".join(paths) if paths else "affected files"
        items.append(
            {
                "query": (
                    f"Investigate whether {count} static {severity} {category} "
                    f"finding(s) indicate broader runtime or cross-file logic flaws "
                    f"around {path_text}"
                ),
                "reason": "static_finding_followup",
                "priority": _priority(severity),
                "category": category,
                "top_k": 3,
            }
        )

    return items


def _append_unique(
    plan: list[dict[str, object]],
    seen_queries: set[str],
    item: dict[str, object],
) -> None:
    query = _string(item.get("query"))
    normalized_query = " ".join(query.lower().split())
    if not normalized_query or normalized_query in seen_queries:
        return

    seen_queries.add(normalized_query)
    plan.append(item)


def _priority(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in HIGH_PRIORITY_VALUES:
        return "high"
    if normalized in {"p1", "medium"}:
        return "medium"
    return "low"


def _priority_rank(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def _highest_priority(rules: list[dict[str, object]]) -> str:
    priorities = [_string(rule.get("priority")) for rule in rules]
    return min(priorities, key=_priority_rank, default="low")


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
