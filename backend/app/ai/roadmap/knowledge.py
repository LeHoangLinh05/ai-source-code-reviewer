"""Load roadmap requirements as natural-language knowledge documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from app.ai.rag.ingestion import RAGDocument

ROADMAP_PROFILE_ID = "roadmap_bootcamp_v1"
ROADMAP_SOURCE_PATH = Path(__file__).with_name("roadmap_rules_v2.yaml")


@dataclass(slots=True, frozen=True)
class RoadmapRequirement:
    """One validated requirement from the roadmap knowledge source."""

    rule_id: str
    week: int | str
    priority: str
    skill_group: str
    requirement: str
    category: str = "requirement"
    check_type: str | None = None
    needs_ai_verification: bool = False
    verification_hint: str | None = None
    rationale: str | None = None

    def to_rag_document(self) -> RAGDocument:
        """Convert the requirement into one knowledge-base document."""

        content_parts = [
            f"Rule ID: {self.rule_id}",
            f"Week: {self.week}",
            f"Priority: {self.priority}",
            f"Skill group: {self.skill_group}",
            f"Category: {self.category}",
            f"Requirement: {self.requirement}",
        ]
        if self.check_type:
            content_parts.append(f"Check type: {self.check_type}")
        if self.verification_hint:
            content_parts.append(f"Verification hint: {self.verification_hint}")
        if self.rationale:
            content_parts.append(f"Rationale: {self.rationale}")

        return RAGDocument(
            source=ROADMAP_PROFILE_ID,
            content="\n".join(content_parts),
            language="general",
            doc_type="roadmap_rule",
            category="requirement",
            extra_metadata={
                "rule_id": self.rule_id,
                "profile_id": ROADMAP_PROFILE_ID,
                "week": self.week,
                "priority": self.priority,
                "skill_group": self.skill_group,
                "check_type": self.check_type,
                "needs_ai_verification": self.needs_ai_verification,
            },
        )


def load_roadmap_requirements(
    source_path: Path = ROADMAP_SOURCE_PATH,
) -> list[RoadmapRequirement]:
    """Load and validate all roadmap requirements from YAML."""

    raw_data = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict) or not isinstance(raw_data.get("rules"), list):
        raise ValueError("Roadmap knowledge source must contain a 'rules' list")

    requirements = [
        _parse_requirement(raw_rule, index=index)
        for index, raw_rule in enumerate(raw_data["rules"])
    ]
    rule_ids = [requirement.rule_id for requirement in requirements]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Roadmap knowledge source contains duplicate rule_id values")

    return requirements


def load_roadmap_documents(
    source_path: Path = ROADMAP_SOURCE_PATH,
) -> list[RAGDocument]:
    """Return every roadmap requirement as a knowledge-base document."""

    return [
        requirement.to_rag_document()
        for requirement in load_roadmap_requirements(source_path)
    ]


def _parse_requirement(raw_rule: object, *, index: int) -> RoadmapRequirement:
    if not isinstance(raw_rule, dict):
        raise ValueError(f"Roadmap rule at index {index} must be an object")

    return RoadmapRequirement(
        rule_id=_required_string(raw_rule, "rule_id", index=index),
        week=_required_week(raw_rule, index=index),
        priority=_required_string(raw_rule, "priority", index=index),
        skill_group=_required_string(raw_rule, "skill_group", index=index),
        category=_optional_string(raw_rule.get("category")) or "requirement",
        check_type=_optional_string(raw_rule.get("check_type")),
        needs_ai_verification=raw_rule.get("needs_ai_verification") is True,
        requirement=_required_string(raw_rule, "requirement", index=index),
        verification_hint=_verification_hint(raw_rule),
        rationale=_optional_string(
            raw_rule.get("rationale") or raw_rule.get("rationale_if_missing")
        ),
    )


def _required_string(
    raw_rule: dict[object, object],
    field_name: str,
    *,
    index: int,
) -> str:
    value = raw_rule.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Roadmap rule at index {index} has invalid {field_name!r}")

    return value.strip()


def _required_week(raw_rule: dict[object, object], *, index: int) -> int | str:
    week = raw_rule.get("week")
    if isinstance(week, int) and not isinstance(week, bool):
        return week
    if isinstance(week, str) and week.strip():
        return week.strip()

    raise ValueError(f"Roadmap rule at index {index} has invalid 'week'")


def _verification_hint(raw_rule: dict[object, object]) -> str | None:
    explicit_hint = _optional_string(
        raw_rule.get("verification_hint") or raw_rule.get("ai_hint")
    )
    legacy_check_type = _optional_string(raw_rule.get("check_type"))
    legacy_target = raw_rule.get("target")

    hint_parts = [explicit_hint] if explicit_hint else []
    if legacy_check_type:
        hint_parts.append(f"Inspect evidence related to {legacy_check_type}.")
    if isinstance(legacy_target, dict) and legacy_target:
        hint_parts.append(f"Historical evidence hints: {legacy_target!s}")

    return " ".join(hint_parts) or None


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
