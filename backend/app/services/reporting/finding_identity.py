"""Canonical identity for raw review findings from every detector."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.models.review_issue import IssueCategory, IssueSource, ReviewIssue

FINDING_KEY_FIELD = "finding_key"
PROBE_REVIEW_FIELD = "probe_review"
CLAIM_TYPE_FIELD = "claim_type"
DEFAULT_CLAIM_TYPE = "review_finding"
ISSUE_RULE_ID_FIELDS = ("test_id", "rule_id", "ruleId", "code")
PROBE_ID_FIELD = "probe_id"
GENERIC_CLAIM_TYPES = {
    "hardcoded_credentials",
    "review_finding",
    "security_finding",
    "sensitive_data_exposure",
}
CONTEXTUAL_OTP_WEAK_RANDOMNESS_CLAIMS = {
    "cryptographically_insecure_randomness",
    "insecure_otp_generation",
    "insecure_randomness",
    "non_cryptographic_otp_generation",
    "non_cryptographic_randomness",
    "predictable_otp_generation",
    "predictable_randomness",
    "weak_cryptographic_practice",
    "weak_otp_generation",
    "weak_randomness",
}
CONTEXTUAL_PASSWORD_HASH_CLAIMS = {
    "weak_cryptographic_practice",
    "weak_cryptography",
}
CLAIM_CATEGORY_OVERRIDES: dict[str, IssueCategory] = {
    "bare_except": IssueCategory.BUG,
    "command_injection": IssueCategory.SECURITY,
    "hardcoded_secret": IssueCategory.SECURITY,
    "n_plus_one_query": IssueCategory.PERFORMANCE,
    "sql_injection": IssueCategory.SECURITY,
    "weak_password_hash": IssueCategory.SECURITY,
}
ROADMAP_FINDING_NAMESPACE = "roadmap"

CLAIM_TYPE_ALIASES: dict[str, str] = {
    "arbitrary_code_execution_via_pickle": "unsafe_deserialization",
    "default_admin_credentials": "insecure_default_credentials",
    "hardcoded_admin_credentials": "insecure_default_credentials",
    "hardcoded_admin_password": "insecure_default_credentials",
    "insecure_deserialization": "unsafe_deserialization",
    "insecure_deserialization_via_pickle": "unsafe_deserialization",
    "insecure_default_admin_credentials": "insecure_default_credentials",
    "missing_authorization_check": "missing_object_level_authorization",
    "missing_authorization_check_in_delete": "missing_object_level_authorization",
    "missing_otp_authentication": "otp_missing_authentication",
    "missing_otp_rate_limit": "otp_missing_rate_limit",
    "n_1_query": "n_plus_one_query",
    "os_command_injection": "command_injection",
    "otp_brute_force": "otp_missing_rate_limit",
    "otp_disclosure": "otp_exposure",
    "otp_leakage": "otp_exposure",
    "otp_no_expiration": "otp_lifecycle",
    "otp_plaintext_persistence": "otp_plaintext_storage",
    "otp_predictable_generation": "otp_weak_randomness",
    "password_logged_in_plaintext": "sensitive_data_logging",
    "performance_n_plus_one": "n_plus_one_query",
    "pickle_deserialization": "unsafe_deserialization",
    "plaintext_password_logging": "sensitive_data_logging",
    "sensitive_data_in_logs": "sensitive_data_logging",
    "server_side_request_forgery": "ssrf",
    "ssrf_external_calls": "ssrf",
    "unsafe_deserialization_via_pickle": "unsafe_deserialization",
}

PROBE_CLAIM_TYPES: dict[str, str] = {
    "security.command_injection": "command_injection",
    "security.hardcoded_secret": "hardcoded_secret",
    "security.insecure_default_credentials": "insecure_default_credentials",
    "security.open_redirect": "open_redirect",
    "security.sensitive_data_logging": "sensitive_data_logging",
    "security.ssrf_external_calls": "ssrf",
    "security.sql_nosql_injection": "sql_injection",
    "security.unsafe_deserialization": "unsafe_deserialization",
    "security.unrestricted_file_upload": "unrestricted_file_upload",
    "security.weak_password_hash": "weak_password_hash",
}

STATIC_RULE_CLAIM_TYPES: dict[str, str] = {
    "B301": "unsafe_deserialization",
    "B403": "unsafe_deserialization",
    "S301": "unsafe_deserialization",
    "S403": "unsafe_deserialization",
    "B110": "bare_except",
    "S110": "bare_except",
    "B105": "hardcoded_secret",
    "B106": "hardcoded_secret",
    "B107": "hardcoded_secret",
    "S105": "hardcoded_secret",
    "S106": "hardcoded_secret",
    "S107": "hardcoded_secret",
    "B324": "weak_cryptography",
    "S324": "weak_cryptography",
    "B602": "command_injection",
    "B605": "command_injection",
    "S602": "command_injection",
    "S605": "command_injection",
    "B608": "sql_injection",
    "S608": "sql_injection",
}

TITLE_CLAIM_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("otp leakage", "otp exposed", "debug_otp"), "otp_exposure"),
    (
        ("default admin credential", "default credential"),
        "insecure_default_credentials",
    ),
    (("unsafe deserial", "pickle"), "unsafe_deserialization"),
    (
        ("hardcoded secret", "hard-coded secret", "hardcoded password", "jwt secret"),
        "hardcoded_secret",
    ),
    (("server-side request forgery", "ssrf"), "ssrf"),
    (
        (
            "sensitive data logging",
            "credential logged",
            "password logged",
            "plaintext password",
        ),
        "sensitive_data_logging",
    ),
    (("sql injection",), "sql_injection"),
)

CLAIM_TOKEN_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("unsafe", "deserial"), "unsafe_deserialization"),
    (("insecure", "deserial"), "unsafe_deserialization"),
    (("pickle", "deserial"), "unsafe_deserialization"),
    (("pickle", "code", "execution"), "unsafe_deserialization"),
    (("server", "side", "request", "forgery"), "ssrf"),
    (("ssrf",), "ssrf"),
    (("otp", "missing", "authentication"), "otp_missing_authentication"),
    (("otp", "unauthenticated"), "otp_missing_authentication"),
    (("otp", "rate", "limit"), "otp_missing_rate_limit"),
    (("otp", "brute", "force"), "otp_missing_rate_limit"),
    (("otp", "plain", "storage"), "otp_plaintext_storage"),
    (("otp", "exposure"), "otp_exposure"),
    (("otp", "disclosure"), "otp_exposure"),
    (("otp", "weak", "random"), "otp_weak_randomness"),
    (("otp", "predictable"), "otp_weak_randomness"),
    (("otp", "expiry"), "otp_lifecycle"),
    (("otp", "reuse"), "otp_lifecycle"),
    (("default", "admin", "credential"), "insecure_default_credentials"),
    (("default", "admin", "password"), "insecure_default_credentials"),
)


def normalize_claim_type(claim_type: str | None, *, fallback_title: str) -> str:
    """Return a canonical snake_case claim identifier."""

    source = claim_type.strip() if claim_type and claim_type.strip() else fallback_title
    normalized = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    normalized = normalized or DEFAULT_CLAIM_TYPE
    aliased = CLAIM_TYPE_ALIASES.get(normalized, normalized)
    tokens = frozenset(aliased.split("_"))
    for required_tokens, canonical_claim_type in CLAIM_TOKEN_PATTERNS:
        if all(token in tokens for token in required_tokens):
            return canonical_claim_type
    if aliased in GENERIC_CLAIM_TYPES:
        title_claim_type = _claim_type_from_title(fallback_title)
        if title_claim_type is not None:
            return title_claim_type
    return aliased


def canonical_probe_claim_type(
    *,
    probe_id: str,
    claim_type: str | None,
    fallback_title: str,
    context_text: str = "",
) -> str:
    """Return the deterministic claim family for one AI probe result."""

    normalized_claim_type = PROBE_CLAIM_TYPES.get(probe_id) or normalize_claim_type(
        claim_type,
        fallback_title=fallback_title,
    )
    return _contextual_claim_type(
        normalized_claim_type,
        context_text=f"{fallback_title}\n{context_text}",
    )


def normalize_finding_path(file_path: str | None) -> str:
    """Return a stable repository-relative path for occurrence matching."""

    if not file_path:
        return ""

    normalized = file_path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()


def build_finding_key(
    *,
    category: IssueCategory | str,
    claim_type: str | None,
    title: str,
    roadmap_rule_id: str | None = None,
) -> str:
    """Build the category-scoped identity used by report grouping."""

    if roadmap_rule_id:
        normalized_rule_id = _normalize_identifier(roadmap_rule_id)
        return f"{ROADMAP_FINDING_NAMESPACE}:{normalized_rule_id}"

    normalized_claim = normalize_claim_type(claim_type, fallback_title=title)
    canonical_category = CLAIM_CATEGORY_OVERRIDES.get(normalized_claim, category)
    category_value = (
        canonical_category.value
        if isinstance(canonical_category, IssueCategory)
        else canonical_category
    )
    return f"{category_value.strip().lower()}:{normalized_claim}"


def review_issue_claim_type(issue: ReviewIssue) -> str:
    """Return a shared claim family for AI, KB, and static findings."""

    raw_output = issue.raw_output if isinstance(issue.raw_output, dict) else {}
    probe_review = raw_output.get(PROBE_REVIEW_FIELD)
    claim_type = _detector_claim_type(raw_output, probe_review)

    if not claim_type:
        claim_type = _claim_type_from_title(issue.title)

    if not claim_type:
        rule_id = _issue_rule_id(raw_output)
        claim_type = f"{issue.source.value}_{rule_id}" if rule_id else issue.title

    normalized_claim_type = normalize_claim_type(
        claim_type,
        fallback_title=issue.title,
    )
    return _contextual_claim_type(
        normalized_claim_type,
        context_text=_review_issue_context_text(issue),
    )


def review_issue_finding_key(issue: ReviewIssue) -> str:
    """Return the canonical category and claim-family key for an issue."""

    return build_finding_key(
        category=issue.category,
        claim_type=review_issue_claim_type(issue),
        title=issue.title,
        roadmap_rule_id=_roadmap_rule_id(issue),
    )


def _detector_claim_type(
    raw_output: dict[str, object],
    probe_review: object,
) -> str | None:
    rule_id = _issue_rule_id(raw_output)
    static_claim_type = STATIC_RULE_CLAIM_TYPES.get((rule_id or "").upper())
    if static_claim_type is not None:
        return static_claim_type

    if isinstance(probe_review, dict):
        probe_id = probe_review.get(PROBE_ID_FIELD)
        if isinstance(probe_id, str) and probe_id in PROBE_CLAIM_TYPES:
            return PROBE_CLAIM_TYPES[probe_id]
        probe_claim_type = probe_review.get(CLAIM_TYPE_FIELD)
        if isinstance(probe_claim_type, str):
            return probe_claim_type

    raw_claim_type = raw_output.get(CLAIM_TYPE_FIELD)
    if isinstance(raw_claim_type, str):
        return raw_claim_type

    stored_key = raw_output.get(FINDING_KEY_FIELD)
    if not isinstance(stored_key, str) or not stored_key.strip():
        return None
    _, separator, stored_claim_type = stored_key.strip().partition(":")
    return stored_claim_type if separator else stored_key


def _roadmap_rule_id(issue: ReviewIssue) -> str | None:
    if issue.source is not IssueSource.KB or not isinstance(issue.raw_output, dict):
        return None
    probe_review = issue.raw_output.get(PROBE_REVIEW_FIELD)
    if not isinstance(probe_review, dict):
        return None
    rule_id = probe_review.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        return None
    return rule_id.strip()


def _issue_rule_id(raw_output: dict[str, object]) -> str | None:
    for field in ISSUE_RULE_ID_FIELDS:
        value = raw_output.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _claim_type_from_title(title: str) -> str | None:
    normalized_title = title.casefold()
    for terms, claim_type in TITLE_CLAIM_PATTERNS:
        if any(term in normalized_title for term in terms):
            return claim_type
    return None


def _contextual_claim_type(claim_type: str, *, context_text: str) -> str:
    """Resolve broad AI labels only when deterministic source context supports it."""

    title_claim_type = _claim_type_from_title(context_text)
    if (
        claim_type == "insecure_default_credentials"
        and title_claim_type == "hardcoded_secret"
    ):
        return "hardcoded_secret"
    if claim_type in CONTEXTUAL_PASSWORD_HASH_CLAIMS and _is_password_hash_context(
        context_text
    ):
        return "weak_password_hash"
    if (
        claim_type in CONTEXTUAL_OTP_WEAK_RANDOMNESS_CLAIMS
        and _is_insecure_otp_randomness_context(context_text)
    ):
        return "otp_weak_randomness"
    return claim_type


def _is_password_hash_context(context_text: str) -> bool:
    tokens = frozenset(re.findall(r"[a-z0-9]+", context_text.casefold()))
    has_password_context = bool({"credential", "password", "passwords"} & tokens)
    has_hash_context = bool(
        {"hash", "hashed", "hashing", "hashlib", "md5", "sha1"} & tokens
    )
    return has_password_context and has_hash_context


def _is_insecure_otp_randomness_context(context_text: str) -> bool:
    tokens = frozenset(re.findall(r"[a-z0-9]+", context_text.casefold()))
    has_otp_context = "otp" in tokens or {
        "one",
        "time",
        "password",
    }.issubset(tokens)
    has_insecure_randomness = (
        "randint" in tokens
        or "predictable" in tokens
        or {"pseudo", "random"}.issubset(tokens)
        or {"non", "cryptographic", "random"}.issubset(tokens)
        or (bool({"weak", "insecure"} & tokens) and "random" in tokens)
    )
    return has_otp_context and has_insecure_randomness


def _review_issue_context_text(issue: ReviewIssue) -> str:
    context_parts = [issue.title, issue.description, issue.suggestion or ""]
    raw_output = issue.raw_output if isinstance(issue.raw_output, dict) else {}
    code = raw_output.get("code")
    if isinstance(code, str):
        context_parts.append(code)
    context_parts.extend(_source_context_lines(raw_output.get("source_context")))
    probe_review = raw_output.get(PROBE_REVIEW_FIELD)
    if not isinstance(probe_review, dict):
        return "\n".join(context_parts)

    context_parts.extend(_source_context_lines(probe_review.get("source_context")))
    return "\n".join(context_parts)


def _source_context_lines(source_context: object) -> list[str]:
    if not isinstance(source_context, dict):
        return []
    lines = source_context.get("lines")
    if not isinstance(lines, list):
        return []
    return [str(line) for line in lines]


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
