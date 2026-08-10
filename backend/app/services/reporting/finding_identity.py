"""Canonical identity for raw review findings from every detector."""

from __future__ import annotations

import re

from app.models.review_issue import IssueCategory, ReviewIssue

FINDING_KEY_FIELD = "finding_key"
PROBE_REVIEW_FIELD = "probe_review"
CLAIM_TYPE_FIELD = "claim_type"
DEFAULT_CLAIM_TYPE = "review_finding"
ISSUE_RULE_ID_FIELDS = ("code", "test_id", "rule_id", "ruleId")

CLAIM_TYPE_ALIASES: dict[str, str] = {
    "missing_authorization_check": "missing_object_level_authorization",
    "missing_authorization_check_in_delete": "missing_object_level_authorization",
    "password_logged_in_plaintext": "sensitive_data_logging",
    "plaintext_password_logging": "sensitive_data_logging",
    "sensitive_data_in_logs": "sensitive_data_logging",
}

STATIC_RULE_CLAIM_TYPES: dict[str, str] = {
    "B301": "unsafe_deserialization",
    "B403": "unsafe_deserialization",
    "S301": "unsafe_deserialization",
    "S403": "unsafe_deserialization",
    "B105": "hardcoded_secret",
    "B106": "hardcoded_secret",
    "B107": "hardcoded_secret",
    "S105": "hardcoded_secret",
    "S106": "hardcoded_secret",
    "S107": "hardcoded_secret",
    "B608": "sql_injection",
    "S608": "sql_injection",
}

TITLE_CLAIM_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
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


def normalize_claim_type(claim_type: str | None, *, fallback_title: str) -> str:
    """Return a canonical snake_case claim identifier."""

    source = claim_type.strip() if claim_type and claim_type.strip() else fallback_title
    normalized = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
    normalized = normalized or DEFAULT_CLAIM_TYPE
    return CLAIM_TYPE_ALIASES.get(normalized, normalized)


def build_finding_key(
    *,
    category: IssueCategory | str,
    claim_type: str | None,
    title: str,
) -> str:
    """Build the category-scoped identity used by report grouping."""

    category_value = category.value if isinstance(category, IssueCategory) else category
    normalized_claim = normalize_claim_type(claim_type, fallback_title=title)
    return f"{category_value.strip().lower()}:{normalized_claim}"


def review_issue_claim_type(issue: ReviewIssue) -> str:
    """Return a shared claim family for AI, KB, and static findings."""

    raw_output = issue.raw_output if isinstance(issue.raw_output, dict) else {}
    probe_review = raw_output.get(PROBE_REVIEW_FIELD)
    claim_type = (
        probe_review.get(CLAIM_TYPE_FIELD) if isinstance(probe_review, dict) else None
    )
    if not isinstance(claim_type, str):
        claim_type = raw_output.get(CLAIM_TYPE_FIELD)

    stored_key = raw_output.get(FINDING_KEY_FIELD)
    if isinstance(stored_key, str) and stored_key.strip():
        _, separator, stored_claim_type = stored_key.strip().partition(":")
        claim_type = stored_claim_type if separator else stored_key

    if not isinstance(claim_type, str) or not claim_type.strip():
        rule_id = _issue_rule_id(raw_output)
        claim_type = STATIC_RULE_CLAIM_TYPES.get((rule_id or "").upper())

    if not claim_type:
        claim_type = _claim_type_from_title(issue.title)

    if not claim_type:
        rule_id = _issue_rule_id(raw_output)
        claim_type = f"{issue.source.value}_{rule_id}" if rule_id else issue.title

    return normalize_claim_type(claim_type, fallback_title=issue.title)


def review_issue_finding_key(issue: ReviewIssue) -> str:
    """Return the canonical category and claim-family key for an issue."""

    return build_finding_key(
        category=issue.category,
        claim_type=review_issue_claim_type(issue),
        title=issue.title,
    )


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
