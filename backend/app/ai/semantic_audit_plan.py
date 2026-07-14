"""Build dynamic semantic audit guidance for the Review Agent."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
import re

MAX_AUDIT_PLAN_ITEMS = 96
MAX_ROADMAP_RULES_PER_PROBE = 3
MAX_PROBE_AUDIT_ITEMS = 88
MAX_FILE_AUDIT_ITEMS = 4
MAX_STATIC_AUDIT_ITEMS = 2

HIGH_PRIORITY_VALUES = {"p0", "critical", "high"}

BASELINE_PROBES: tuple[dict[str, object], ...] = (
    {
        "probe_id": "security.sql_nosql_injection",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find request parameters used in SQL, ORM, NoSQL, filter, where, raw "
            "query, or aggregation calls without parameter binding or validation"
        ),
    },
    {
        "probe_id": "security.command_injection",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find shell, process, subprocess, exec, system, spawn, or command "
            "calls that include request-controlled input"
        ),
    },
    {
        "probe_id": "security.xss_template_injection",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find unescaped HTML, template rendering, dangerouslySetInnerHTML, "
            "markdown, or user content rendered without sanitization"
        ),
    },
    {
        "probe_id": "security.ssrf_external_calls",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find outbound HTTP, fetch, requests, axios, webhook, file, or URL "
            "loads built from request input without allowlist validation"
        ),
    },
    {
        "probe_id": "security.unsafe_deserialization",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find pickle, yaml.load, eval, dynamic import, object deserialization, "
            "or parser calls on untrusted input"
        ),
    },
    {
        "probe_id": "security.hardcoded_secret",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find hardcoded passwords, API keys, JWT secrets, tokens, private keys, "
            "or fallback credentials in source or config"
        ),
    },
    {
        "probe_id": "security.jwt_session_auth",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find login, refresh, logout, JWT, session, cookie, password hashing, "
            "token expiry, token validation, and revocation behavior"
        ),
    },
    {
        "probe_id": "security.authorization",
        "category": "security",
        "priority": "high",
        "risk_area": "security",
        "query": (
            "Find authorization checks for user ownership, roles, permissions, "
            "tenant boundaries, admin routes, and object-level access"
        ),
    },
    {
        "probe_id": "security.input_validation_cors",
        "category": "security",
        "priority": "medium",
        "risk_area": "security",
        "query": (
            "Find request body, query, header, file upload, CORS, and boundary "
            "validation behavior that accepts unsafe or overly broad input"
        ),
    },
    {
        "probe_id": "bug.error_none_edges",
        "category": "bug",
        "priority": "medium",
        "risk_area": "bug",
        "query": (
            "Find missing error handling, None/null edge cases, unchecked optional "
            "values, invalid states, and response paths that can crash"
        ),
    },
    {
        "probe_id": "bug.swallowed_exceptions",
        "category": "bug",
        "priority": "medium",
        "risk_area": "bug",
        "query": (
            "Find broad except blocks, swallowed exceptions, failed retries, and "
            "fallback behavior that hides runtime failures"
        ),
    },
    {
        "probe_id": "bug.async_concurrency",
        "category": "bug",
        "priority": "medium",
        "risk_area": "bug",
        "query": (
            "Find async tasks, background jobs, websocket handlers, race-prone "
            "shared state, and fire-and-forget work without supervision"
        ),
    },
    {
        "probe_id": "bug.state_transaction_consistency",
        "category": "bug",
        "priority": "medium",
        "risk_area": "bug",
        "query": (
            "Find multi-step state changes, database writes, commits, rollbacks, "
            "cache updates, or external calls that can leave inconsistent state"
        ),
    },
    {
        "probe_id": "bug.incomplete_branches",
        "category": "bug",
        "priority": "medium",
        "risk_area": "bug",
        "query": (
            "Find incomplete branches, TODO pass-through logic, hardcoded returns, "
            "stub implementations, and unreachable or unhandled cases"
        ),
    },
    {
        "probe_id": "performance.n_plus_one",
        "category": "performance",
        "priority": "medium",
        "risk_area": "performance",
        "query": (
            "Find loops that call database queries, repository methods, ORM loads, "
            "HTTP clients, or other I/O per item"
        ),
    },
    {
        "probe_id": "performance.pagination_bounds",
        "category": "performance",
        "priority": "medium",
        "risk_area": "performance",
        "query": (
            "Find list, search, export, feed, sync, or query endpoints without "
            "pagination, limits, streaming, or bounded iteration"
        ),
    },
    {
        "probe_id": "performance.repeated_external_calls",
        "category": "performance",
        "priority": "medium",
        "risk_area": "performance",
        "query": (
            "Find repeated external API calls, network requests, embedding calls, "
            "or expensive work inside request paths without batching or caching"
        ),
    },
    {
        "probe_id": "performance.memory_serialization_cache",
        "category": "performance",
        "priority": "medium",
        "risk_area": "performance",
        "query": (
            "Find memory growth, large in-memory collections, inefficient "
            "serialization, stale cache behavior, and cache misuse"
        ),
    },
    {
        "probe_id": "maintainability.dead_complex_code",
        "category": "maintainability",
        "priority": "medium",
        "risk_area": "maintainability",
        "query": (
            "Find dead code, unused functions, overly complex functions, deep "
            "nesting, and branches that are hard to reason about"
        ),
    },
    {
        "probe_id": "maintainability.duplication_side_effects",
        "category": "maintainability",
        "priority": "medium",
        "risk_area": "maintainability",
        "query": (
            "Find duplicate business logic, hidden side effects, unclear state "
            "mutation, long parameter lists, and misleading names"
        ),
    },
    {
        "probe_id": "maintainability.layering_imports",
        "category": "maintainability",
        "priority": "medium",
        "risk_area": "maintainability",
        "query": (
            "Find API/service/repository layering violations, circular imports, "
            "business logic in routes, and infrastructure constructed in services"
        ),
    },
    {
        "probe_id": "style.boundary_contracts",
        "category": "style",
        "priority": "low",
        "risk_area": "general",
        "query": (
            "Find missing schema validation at boundaries, inconsistent response "
            "contracts, unclear API names, and convention violations with user impact"
        ),
    },
    {
        "probe_id": "style.production_diagnostics",
        "category": "style",
        "priority": "low",
        "risk_area": "general",
        "query": (
            "Find production prints, debug leftovers, commented-out code, noisy "
            "diagnostics, and operational readability issues"
        ),
    },
)


def build_semantic_audit_plan(
    *,
    roadmap_context: dict[str, object] | None,
    files_to_review: Sequence[dict[str, object]],
    static_issues: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Return dynamic semantic-search seeds without hard-coding benchmark bugs."""

    plan: list[dict[str, object]] = []
    seen_queries: set[str] = set()

    for item in _probe_audit_items(
        roadmap_context=roadmap_context,
        files_to_review=files_to_review,
    ):
        _append_unique(plan, seen_queries, item)

    for item in _file_audit_items(files_to_review):
        _append_unique(plan, seen_queries, item)

    for item in _static_audit_items(static_issues):
        _append_unique(plan, seen_queries, item)

    return plan[:MAX_AUDIT_PLAN_ITEMS]


def _probe_audit_items(
    *,
    roadmap_context: dict[str, object] | None,
    files_to_review: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    roadmap_rules_by_category = _roadmap_rules_by_category(roadmap_context)
    baseline_probes_by_category = _baseline_probes_by_category()
    path_hints_by_category = _path_hints_by_category(files_to_review)
    categories = _ordered_categories(
        baseline_by_category=baseline_probes_by_category,
        roadmap_rules_by_category=roadmap_rules_by_category,
    )

    items: list[dict[str, object]] = []
    for category in categories:
        path_hints = path_hints_by_category.get(category, [])
        for baseline_probe in baseline_probes_by_category.get(category, []):
            items.append(
                _baseline_probe_item(
                    baseline_probe=baseline_probe,
                    path_hints=path_hints,
                )
            )

        for probe_index, rule_batch in enumerate(
            _roadmap_rule_probe_batches(roadmap_rules_by_category.get(category, [])),
            start=1,
        ):
            items.append(
                _roadmap_probe_item(
                    category=category,
                    probe_index=probe_index,
                    path_hints=path_hints,
                    rules=rule_batch,
                )
            )

    return items[:MAX_PROBE_AUDIT_ITEMS]


def _roadmap_rules_by_category(
    roadmap_context: dict[str, object] | None,
) -> dict[str, list[dict[str, object]]]:
    if roadmap_context is None:
        return {}

    raw_rules = roadmap_context.get("review_rules")
    if not isinstance(raw_rules, list):
        raw_rules = roadmap_context.get("ai_verification_rules", [])
    if not isinstance(raw_rules, list):
        return {}

    grouped_rules: dict[str, list[dict[str, object]]] = {}
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue

        rule_id = _string(raw_rule.get("rule_id"))
        requirement = _string(raw_rule.get("requirement"))
        verification_hint = _string(raw_rule.get("verification_hint"))
        skill_group = _string(raw_rule.get("skill_group"))
        review_category = _string(raw_rule.get("review_category")) or "requirement"
        check_type = _string(raw_rule.get("check_type"))
        priority = _string(raw_rule.get("priority")) or "P1"
        needs_ai_verification = raw_rule.get("needs_ai_verification") is True
        if not rule_id or not (requirement or verification_hint):
            continue

        grouped_rules.setdefault(review_category, []).append(
            {
                "rule_id": rule_id,
                "skill_group": skill_group,
                "requirement": requirement,
                "verification_hint": verification_hint,
                "review_category": review_category,
                "check_type": check_type,
                "needs_ai_verification": needs_ai_verification,
                "priority": _priority(priority),
            }
        )

    return grouped_rules


def _baseline_probes_by_category() -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for probe in BASELINE_PROBES:
        category = _string(probe.get("category"))
        if category:
            grouped.setdefault(category, []).append(probe)
    return grouped


def _ordered_categories(
    *,
    baseline_by_category: dict[str, list[dict[str, object]]],
    roadmap_rules_by_category: dict[str, list[dict[str, object]]],
) -> list[str]:
    categories: list[str] = []
    for probe in BASELINE_PROBES:
        category = _string(probe.get("category"))
        if category and category not in categories:
            categories.append(category)

    remaining_categories = [
        category
        for category in roadmap_rules_by_category
        if category not in baseline_by_category
    ]
    remaining_categories.sort(
        key=lambda category: (
            _priority_rank(_highest_rule_priority(roadmap_rules_by_category[category])),
            category,
        )
    )
    categories.extend(remaining_categories)
    return categories


def _sorted_rules(rules: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rules,
        key=lambda rule: (
            not bool(rule.get("needs_ai_verification")),
            _priority_rank(_string(rule.get("priority"))),
            str(rule["rule_id"]),
        ),
    )


def _roadmap_rule_probe_batches(
    rules: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    grouped_rules: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for rule in _sorted_rules(rules):
        category = _string(rule.get("review_category")) or "requirement"
        skill_group = _slug(_string(rule.get("skill_group")) or "general")
        check_type = _slug(_string(rule.get("check_type")) or "behavior")
        grouped_rules.setdefault((category, skill_group, check_type), []).append(rule)

    batches: list[list[dict[str, object]]] = []
    for key in sorted(grouped_rules):
        batches.extend(_batched(grouped_rules[key], MAX_ROADMAP_RULES_PER_PROBE))
    return batches


def _baseline_probe_item(
    *,
    baseline_probe: dict[str, object],
    path_hints: list[str],
) -> dict[str, object]:
    category = _string(baseline_probe.get("category")) or "maintainability"
    probe_id = _string(baseline_probe.get("probe_id")) or f"{category}.baseline"
    query_parts = [
        f"Probe {probe_id} in {category} category",
        _string(baseline_probe.get("query")),
    ]
    if path_hints:
        query_parts.append(f"prioritize likely paths: {', '.join(path_hints[:5])}")
    query_parts.append(_probe_evidence_instruction())

    priority = _string(baseline_probe.get("priority")) or "medium"
    return {
        "audit_plan_item_id": f"category_probe:{probe_id}",
        "probe_id": probe_id,
        "probe_kind": "baseline",
        "query": "; ".join(part for part in query_parts if part),
        "reason": "category_probe",
        "priority": _priority(priority),
        "category": category,
        "review_category": category,
        "risk_area": _risk_area_for_category(category, baseline_probe),
        "source_kinds": ["baseline"],
        "related_rule_ids": [],
        "top_k": 5,
    }


def _roadmap_probe_item(
    *,
    category: str,
    probe_index: int,
    path_hints: list[str],
    rules: list[dict[str, object]],
) -> dict[str, object]:
    rule_ids = [str(rule["rule_id"]) for rule in rules]
    probe_slug = _roadmap_probe_slug(
        category=category,
        probe_index=probe_index,
        rules=rules,
    )
    query_parts = [
        f"Probe {probe_slug} in {category} category",
        f"roadmap rule ids: {', '.join(rule_ids)}",
    ]
    priorities: list[str] = []
    if path_hints:
        query_parts.append(f"prioritize likely paths: {', '.join(path_hints[:5])}")
    skill_groups = _unique_strings(_string(rule.get("skill_group")) for rule in rules)
    if skill_groups:
        query_parts.append(f"focus area: {', '.join(skill_groups[:3])}")
    check_types = _unique_strings(_string(rule.get("check_type")) for rule in rules)
    if check_types:
        query_parts.append(f"check type: {', '.join(check_types[:3])}")
    hints = [
        _truncate(_roadmap_hint_for_query(rule), 180)
        for rule in rules
        if _roadmap_hint_for_query(rule)
    ]
    if hints:
        query_parts.append("behavior intent: " + " | ".join(hints[:3]))
    requirements = [
        _truncate(_string(rule.get("requirement")), 140)
        for rule in rules
        if _string(rule.get("requirement"))
    ]
    if requirements:
        query_parts.append("required behavior: " + " | ".join(requirements[:3]))
    priorities.extend(_string(rule.get("priority")) for rule in rules)
    query_parts.append(_probe_evidence_instruction())

    return {
        "audit_plan_item_id": f"category_probe:{probe_slug}",
        "probe_id": probe_slug,
        "probe_kind": "roadmap",
        "query": "; ".join(query_parts),
        "reason": "category_probe",
        "priority": _highest_priority(priorities),
        "category": category,
        "review_category": category,
        "risk_area": _risk_area_for_category(category, None),
        "source_kinds": ["roadmap"],
        "related_rule_ids": rule_ids,
        "top_k": 5,
    }


def _roadmap_probe_slug(
    *,
    category: str,
    probe_index: int,
    rules: list[dict[str, object]],
) -> str:
    first_rule = rules[0] if rules else {}
    skill_group = _slug(_string(first_rule.get("skill_group")) or "general")
    check_type = _slug(_string(first_rule.get("check_type")) or "behavior")
    first_rule_id = _slug(_string(first_rule.get("rule_id")) or str(probe_index))
    return f"{category}.{skill_group}.{check_type}.{first_rule_id}"


def _probe_evidence_instruction() -> str:
    return (
        "retrieve source/config evidence for real control flow, data flow, "
        "dependencies, integration boundaries, stubs, mocks, hardcoded returns, "
        "fake pass-through logic, incomplete behavior, and contradiction checks"
    )


def _batched(
    items: list[dict[str, object]],
    batch_size: int,
) -> list[list[dict[str, object]]]:
    return [
        items[index : index + batch_size] for index in range(0, len(items), batch_size)
    ]


def _unique_strings(values: Iterable[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value and value not in unique_values:
            unique_values.append(value)
    return unique_values


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3].rstrip()}..."


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "general"


def _path_hints_by_category(
    files_to_review: Sequence[dict[str, object]],
) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {
        "security": [],
        "bug": [],
        "performance": [],
        "maintainability": [],
        "style": [],
    }
    for file_info in files_to_review:
        file_path = _string(file_info.get("file_path"))
        if not file_path:
            continue

        risk_area = _string(file_info.get("risk_area"))
        category = risk_area if risk_area in hints else _category_from_path(file_path)
        if category not in hints:
            category = "maintainability"
        if file_path not in hints[category]:
            hints[category].append(file_path)

    return {category: paths[:5] for category, paths in hints.items() if paths}


def _category_from_path(file_path: str) -> str:
    normalized = file_path.replace("\\", "/").lower()
    path_parts = set(normalized.split("/"))
    if path_parts & {"auth", "security", "crypto", "middleware"}:
        return "security"
    if path_parts & {"db", "database", "models", "repositories"}:
        return "performance"
    if path_parts & {"api", "routes", "routers", "services", "workers"}:
        return "bug"
    return "maintainability"


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


def _highest_priority(priorities: list[str]) -> str:
    normalized_priorities = [_priority(priority) for priority in priorities if priority]
    return min(normalized_priorities, key=_priority_rank, default="low")


def _highest_rule_priority(rules: list[dict[str, object]]) -> str:
    return _highest_priority([_string(rule.get("priority")) for rule in rules])


def _risk_area_for_category(
    category: str,
    baseline_audit: dict[str, object] | None,
) -> str:
    if baseline_audit is not None:
        risk_area = _string(baseline_audit.get("risk_area"))
        if risk_area:
            return risk_area
    if category in {
        "security",
        "bug",
        "performance",
        "maintainability",
        "style",
        "structure",
        "ai",
        "realtime",
        "requirement",
    }:
        return category
    return "general"


def _roadmap_hint_for_query(rule: dict[str, object]) -> str:
    hint = _string(rule.get("verification_hint"))
    if not hint:
        return ""
    if rule.get("needs_ai_verification") is True:
        return hint

    legacy_prefix = "Inspect evidence related to "
    if hint.startswith(legacy_prefix):
        return ""
    return hint.split(" Historical evidence hints:", 1)[0].strip()


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
