"""Build focused, typed probe definitions for semantic source review."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from app.ai.probe_contracts import ProbeDefinition, ProbeLane

MAX_ROADMAP_RULES_PER_PROBE = 3
MAX_FILE_AUDIT_ITEMS = 4
MAX_RETRIEVAL_QUERY_TOKENS = 64
MIN_ROADMAP_TERM_LENGTH = 4
HIGH_PRIORITY_VALUES = {"p0", "critical", "high"}
CATEGORY_TOP_K = {
    "security": 6,
    "bug": 5,
    "performance": 5,
    "maintainability": 4,
    "style": 3,
}


def _baseline(
    *,
    probe_id: str,
    category: str,
    priority: str,
    queries: tuple[str, ...],
    lexical_terms: tuple[str, ...],
    question: str,
    risk_area: str | None = None,
) -> ProbeDefinition:
    return ProbeDefinition(
        probe_id=probe_id,
        lane=ProbeLane.DEFECT,
        category=category,
        priority=priority,
        risk_area=risk_area or category,
        retrieval_queries=queries,
        lexical_terms=lexical_terms,
        judge_question=question,
        top_k=CATEGORY_TOP_K[category],
    )


BASELINE_PROBES: tuple[ProbeDefinition, ...] = (
    _baseline(
        probe_id="security.sql_nosql_injection",
        category="security",
        priority="high",
        queries=(
            "raw SQL execute request parameter string formatting",
            "from_statement text query interpolation concatenation",
        ),
        lexical_terms=("execute", "from_statement", "text", "select", "where"),
        question=(
            "Does request-controlled data reach a SQL, ORM, or NoSQL query without "
            "safe parameter binding or allowlist validation?"
        ),
    ),
    _baseline(
        probe_id="security.command_injection",
        category="security",
        priority="high",
        queries=(
            "subprocess shell true request parameter command execution",
            "system exec spawn user controlled command argument",
        ),
        lexical_terms=("subprocess", "popen", "shell", "system", "exec", "spawn"),
        question=(
            "Does request-controlled data reach a shell or process execution sink "
            "without safe argument separation or validation?"
        ),
    ),
    _baseline(
        probe_id="security.xss_template_injection",
        category="security",
        priority="high",
        queries=(
            "unescaped user HTML template rendering",
            "dangerouslySetInnerHTML markdown sanitization",
        ),
        lexical_terms=("dangerouslysetinnerhtml", "render", "html", "markdown"),
        question=(
            "Can untrusted content be rendered as executable HTML or template code?"
        ),
    ),
    _baseline(
        probe_id="security.ssrf_external_calls",
        category="security",
        priority="high",
        queries=(
            "HTTP client get request URL from route parameter",
            "fetch axios requests webhook user controlled URL allowlist",
        ),
        lexical_terms=("httpx", "requests", "axios", "fetch", "client.get", "url"),
        question=(
            "Can request-controlled input choose an outbound URL without an allowlist, "
            "scheme restriction, or private-network protection?"
        ),
    ),
    _baseline(
        probe_id="security.unsafe_deserialization",
        category="security",
        priority="high",
        queries=(
            "untrusted pickle yaml load eval deserialization",
            "dynamic import parser request payload execution",
        ),
        lexical_terms=("pickle", "yaml.load", "eval", "loads", "deserialize"),
        question="Is untrusted input passed to an unsafe parser or deserializer?",
    ),
    _baseline(
        probe_id="security.hardcoded_secret",
        category="security",
        priority="high",
        queries=(
            "hardcoded password API key JWT secret token source",
            "fallback credential private key configuration",
        ),
        lexical_terms=("password", "secret", "api_key", "token", "private_key"),
        question=(
            "Does production source contain a usable hardcoded credential or secret?"
        ),
    ),
    _baseline(
        probe_id="security.object_authorization",
        category="security",
        priority="high",
        queries=(
            "route object id current user ownership get by id",
            "authenticated endpoint resource user id authorization",
        ),
        lexical_terms=("current_user", "get_by_id", "user_id", "order_id", "owner"),
        question=(
            "Can an authenticated caller read or mutate an object without proving "
            "ownership or equivalent object-level permission?"
        ),
    ),
    _baseline(
        probe_id="security.role_authorization",
        category="security",
        priority="high",
        queries=(
            "admin route role permission dependency authorization",
            "authenticated user privileged operation RBAC",
        ),
        lexical_terms=("admin", "role", "permission", "get_current_user"),
        question=(
            "Can a non-privileged authenticated user invoke a privileged operation?"
        ),
    ),
    _baseline(
        probe_id="security.mass_assignment",
        category="security",
        priority="high",
        queries=(
            "request dictionary setattr model fields update",
            "payload items arbitrary attribute assignment sensitive role",
        ),
        lexical_terms=("payload", "dict", "setattr", "model_dump", "items"),
        question=(
            "Can request data assign arbitrary model attributes, including sensitive "
            "or authorization fields?"
        ),
    ),
    _baseline(
        probe_id="security.weak_password_hash",
        category="security",
        priority="high",
        queries=(
            "password hashing md5 sha1 weak digest",
            "hashlib password update without adaptive hash",
        ),
        lexical_terms=("hashlib", "md5", "sha1", "hexdigest", "password"),
        question="Are passwords stored or updated using a weak, fast hash?",
    ),
    _baseline(
        probe_id="security.reset_token_lifecycle",
        category="security",
        priority="high",
        queries=(
            "password reset token Redis set expiration TTL",
            "reset token generation reuse invalidation expiry",
        ),
        lexical_terms=("reset", "redis.set", "setex", "expire", "ttl", "token"),
        question=(
            "Can a password-reset token remain valid without expiration, secure "
            "generation, or one-time invalidation?"
        ),
    ),
    _baseline(
        probe_id="security.refresh_token_validation",
        category="security",
        priority="high",
        queries=(
            "refresh token validate signature expiry issuer audience rotation",
            "JWT refresh reuse revoke session",
        ),
        lexical_terms=("refresh", "jwt", "decode", "issuer", "audience", "revoke"),
        question="Is refresh-token validation or rotation incomplete or bypassable?",
    ),
    _baseline(
        probe_id="security.logout_revocation",
        category="security",
        priority="high",
        queries=(
            "logout revoke refresh token blacklist session",
            "logout success without invalidating token",
        ),
        lexical_terms=("logout", "revoke", "blacklist", "delete", "session"),
        question="Does logout leave issued credentials usable after returning success?",
    ),
    _baseline(
        probe_id="security.input_validation_cors",
        category="security",
        priority="medium",
        queries=(
            "request boundary schema validation unsafe input",
            "file upload header query CORS overly broad",
        ),
        lexical_terms=("request", "query", "header", "upload", "cors"),
        question="Does an external boundary accept unsafe or overly broad input?",
    ),
    _baseline(
        probe_id="bug.error_none_edges",
        category="bug",
        priority="medium",
        queries=(
            "unchecked optional None invalid state crash",
            "missing error response absent value",
        ),
        lexical_terms=("none", "null", "optional", "raise", "error"),
        question=(
            "Can an unchecked absent value or invalid state cause incorrect behavior?"
        ),
    ),
    _baseline(
        probe_id="bug.swallowed_exceptions",
        category="bug",
        priority="medium",
        queries=(
            "broad exception swallowed failure fallback",
            "except pass retry error hidden",
        ),
        lexical_terms=("except", "pass", "error", "fallback", "retry"),
        question="Is a runtime failure swallowed or converted into misleading success?",
    ),
    _baseline(
        probe_id="bug.async_concurrency",
        category="bug",
        priority="high",
        queries=(
            "read modify write shared state race database",
            "inventory stock check decrement without lock",
            "async task fire and forget unsupervised",
        ),
        lexical_terms=("quantity", "stock", "update", "lock", "task", "await"),
        question=(
            "Can concurrent execution cause a lost update, race, or unsupervised "
            "failure?"
        ),
    ),
    _baseline(
        probe_id="bug.state_transaction_consistency",
        category="bug",
        priority="high",
        queries=(
            "multiple database writes missing rollback transaction",
            "state update external call inconsistent commit",
        ),
        lexical_terms=("commit", "rollback", "transaction", "update", "create"),
        question="Can a failed multi-step operation leave durable state inconsistent?",
    ),
    _baseline(
        probe_id="bug.incomplete_branches",
        category="bug",
        priority="medium",
        queries=(
            "incomplete branch hardcoded return stub TODO",
            "unreachable unhandled behavior pass through",
        ),
        lexical_terms=("todo", "pass", "notimplemented", "hardcoded"),
        question=(
            "Is a reachable behavior incomplete, stubbed, or incorrectly bypassed?"
        ),
    ),
    _baseline(
        probe_id="performance.n_plus_one",
        category="performance",
        priority="high",
        queries=(
            "database execute await inside loop per item",
            "repository query ORM load for each record",
        ),
        lexical_terms=("for", "execute", "await", "repository", "scalars"),
        question="Does iteration perform database or network I/O once per item?",
    ),
    _baseline(
        probe_id="performance.pagination_bounds",
        category="performance",
        priority="medium",
        queries=(
            "load all records filter paginate in memory",
            "list query without limit offset bounded iteration",
        ),
        lexical_terms=("all", "limit", "offset", "page", "size", "filter"),
        question="Can an operation load or process an unbounded result set?",
    ),
    _baseline(
        probe_id="performance.repeated_external_calls",
        category="performance",
        priority="medium",
        queries=(
            "external network request inside loop without batching",
            "repeated expensive call request path cache",
        ),
        lexical_terms=("client", "request", "await", "for", "cache"),
        question="Is expensive I/O repeated where batching or caching is required?",
    ),
    _baseline(
        probe_id="performance.memory_serialization_cache",
        category="performance",
        priority="medium",
        queries=(
            "large in memory collection serialization",
            "cache stale growth misuse expiration",
        ),
        lexical_terms=("list", "all", "serialize", "cache", "expire"),
        question=(
            "Can collection, serialization, or cache behavior cause avoidable growth?"
        ),
    ),
    _baseline(
        probe_id="maintainability.resource_lifecycle",
        category="maintainability",
        priority="high",
        queries=(
            "create database engine client inside method",
            "connection pool infrastructure lifecycle per request",
        ),
        lexical_terms=("create_engine", "create_async_engine", "client", "dispose"),
        question=(
            "Is shared infrastructure recreated inside request or repository logic?"
        ),
    ),
    _baseline(
        probe_id="maintainability.dead_complex_code",
        category="maintainability",
        priority="medium",
        queries=("dead unused complex nested function branches",),
        lexical_terms=("if", "else", "return", "unused"),
        question="Is production logic dead or unnecessarily difficult to reason about?",
    ),
    _baseline(
        probe_id="maintainability.duplication_side_effects",
        category="maintainability",
        priority="medium",
        queries=("duplicate logic hidden state mutation side effect",),
        lexical_terms=("setattr", "append", "update", "global"),
        question="Does duplicated or hidden mutation make behavior unsafe to maintain?",
    ),
    _baseline(
        probe_id="maintainability.layering_imports",
        category="maintainability",
        priority="medium",
        queries=("API service repository layering infrastructure dependency",),
        lexical_terms=("repository", "service", "engine", "session"),
        question="Does code violate a meaningful application-layer ownership boundary?",
    ),
    _baseline(
        probe_id="style.boundary_contracts",
        category="style",
        priority="low",
        risk_area="general",
        queries=("missing boundary schema inconsistent response contract",),
        lexical_terms=("response_model", "schema", "dict", "request"),
        question=(
            "Does a boundary contract create a concrete correctness or safety risk?"
        ),
    ),
    _baseline(
        probe_id="style.production_diagnostics",
        category="style",
        priority="low",
        risk_area="general",
        queries=("production debug print noisy diagnostics commented code",),
        lexical_terms=("print", "debug", "console.log"),
        question="Does leftover diagnostic code create an operational problem?",
    ),
)


def build_semantic_audit_plan(
    *,
    roadmap_context: dict[str, object] | None,
    files_to_review: Sequence[dict[str, object]],
    static_issues: Sequence[dict[str, object]],
) -> list[ProbeDefinition]:
    """Return defect, coverage, and roadmap probes with independent intent."""

    _ = static_issues  # Static findings are intentionally isolated from AI review.
    plan = list(BASELINE_PROBES)
    plan.extend(_coverage_probes(files_to_review))
    plan.extend(_roadmap_probes(roadmap_context))
    return plan


def _coverage_probes(
    files_to_review: Sequence[dict[str, object]],
) -> list[ProbeDefinition]:
    candidates = [
        file_info
        for file_info in files_to_review
        if _string(file_info.get("file_path"))
        and _string(file_info.get("priority")) in {"high", "medium"}
    ]
    candidates.sort(
        key=lambda item: (
            _priority_rank(_string(item.get("priority"))),
            _string(item.get("file_path")),
        )
    )
    probes: list[ProbeDefinition] = []
    for file_info in candidates[:MAX_FILE_AUDIT_ITEMS]:
        file_path = _string(file_info.get("file_path"))
        risk_area = _string(file_info.get("risk_area")) or "general"
        probes.append(
            ProbeDefinition(
                probe_id=f"coverage.{_slug(file_path)}",
                lane=ProbeLane.COVERAGE,
                category=_category_for_risk(risk_area),
                priority=_string(file_info.get("priority")) or "medium",
                risk_area=risk_area,
                retrieval_queries=(
                    "end to end behavior insecure defaults hidden logic "
                    "incomplete branch",
                ),
                lexical_terms=(),
                judge_question=(
                    "Does this high-risk file contain a concrete security, "
                    "correctness, "
                    "performance, or lifecycle defect?"
                ),
                top_k=6,
                file_scope=file_path,
                source_kinds=("coverage",),
                reason="high_risk_file",
                probe_kind="coverage",
            )
        )
    return probes


def _roadmap_probes(
    roadmap_context: dict[str, object] | None,
) -> list[ProbeDefinition]:
    grouped = _roadmap_rules_by_group(roadmap_context)
    probes: list[ProbeDefinition] = []
    for category, rules in sorted(grouped.items()):
        for batch_index, batch in enumerate(
            _batched(_sorted_rules(rules), MAX_ROADMAP_RULES_PER_PROBE),
            start=1,
        ):
            rule_ids = tuple(_string(rule.get("rule_id")) for rule in batch)
            skill_groups = " ".join(_string(rule.get("skill_group")) for rule in batch)
            check_types = " ".join(_string(rule.get("check_type")) for rule in batch)
            intent = " ".join(
                _string(rule.get("verification_hint"))
                or _string(rule.get("requirement"))
                for rule in batch
            )
            requirement = " | ".join(_string(rule.get("requirement")) for rule in batch)
            probes.append(
                ProbeDefinition(
                    probe_id=(f"roadmap.{_slug(category)}.{batch_index}"),
                    lane=ProbeLane.ROADMAP,
                    category=category,
                    priority=_highest_priority(
                        [_string(rule.get("priority")) for rule in batch]
                    ),
                    risk_area=_category_for_risk(category),
                    retrieval_queries=(_bounded_query(intent or requirement),),
                    lexical_terms=tuple(
                        _unique_terms(f"{skill_groups} {check_types} {requirement}")[
                            :12
                        ]
                    ),
                    judge_question=(
                        "Does the source evidence satisfy or contradict these roadmap "
                        f"requirements: {requirement}?"
                    ),
                    top_k=1,
                    related_rule_ids=rule_ids,
                    source_kinds=("roadmap",),
                    probe_kind="roadmap",
                )
            )
    return probes


def _roadmap_rules_by_group(
    roadmap_context: dict[str, object] | None,
) -> dict[str, list[dict[str, object]]]:
    if roadmap_context is None:
        return {}
    raw_rules = roadmap_context.get("review_rules")
    if not isinstance(raw_rules, list):
        raw_rules = roadmap_context.get("ai_verification_rules", [])
    if not isinstance(raw_rules, list):
        return {}

    grouped: dict[str, list[dict[str, object]]] = {}
    for value in raw_rules:
        if not isinstance(value, dict):
            continue
        rule_id = _string(value.get("rule_id"))
        requirement = _string(value.get("requirement"))
        verification_hint = _string(value.get("verification_hint"))
        if not rule_id or not (requirement or verification_hint):
            continue
        category = _string(value.get("review_category")) or "requirement"
        grouped.setdefault(category, []).append(
            {
                **value,
                "rule_id": rule_id,
                "requirement": requirement,
                "verification_hint": verification_hint,
                "priority": _priority(_string(value.get("priority"))),
            }
        )
    return grouped


def _bounded_query(value: str) -> str:
    words = value.split()
    if not words:
        return "verify required runtime behavior from source evidence"
    return " ".join(words[:MAX_RETRIEVAL_QUERY_TOKENS])


def _unique_terms(value: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", value.lower()):
        if len(term) < MIN_ROADMAP_TERM_LENGTH or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _sorted_rules(rules: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        rules,
        key=lambda rule: (
            not bool(rule.get("needs_ai_verification")),
            _priority_rank(_string(rule.get("priority"))),
            _string(rule.get("skill_group")),
            _string(rule.get("check_type")),
            _string(rule.get("rule_id")),
        ),
    )


def _batched(
    items: list[dict[str, object]],
    batch_size: int,
) -> Iterable[list[dict[str, object]]]:
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def _priority(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in HIGH_PRIORITY_VALUES:
        return "high"
    if normalized in {"p1", "medium"}:
        return "medium"
    return "low"


def _priority_rank(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(_priority(value), 3)


def _highest_priority(priorities: list[str]) -> str:
    return min((_priority(value) for value in priorities), key=_priority_rank)


def _category_for_risk(value: str) -> str:
    if value in CATEGORY_TOP_K:
        return value
    return {
        "api": "bug",
        "database": "performance",
        "config": "security",
    }.get(value, "maintainability")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "general"


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
