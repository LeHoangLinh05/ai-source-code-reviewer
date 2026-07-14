"""Select the complete roadmap rule set using knowledge-base metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.ai.rag.vectorstore import get_vectorstore
from app.ai.roadmap.knowledge import (
    ROADMAP_PROFILE_ID,
    RoadmapRequirement,
    load_roadmap_requirements,
)


class KnowledgeDocument(Protocol):
    """Knowledge document metadata needed for roadmap selection."""

    metadata: dict[str, object]


class KnowledgeDocumentStore(Protocol):
    """Storage contract for metadata-only roadmap selection."""

    def all_documents(self) -> list[KnowledgeDocument]: ...


@dataclass(slots=True, frozen=True)
class RoadmapProfile:
    """Validated opt-in roadmap profile from review job options."""

    profile_id: str
    weeks_included: tuple[int, ...] | None


@dataclass(slots=True, frozen=True)
class RoadmapRuleMetadata:
    """Knowledge metadata needed to score and materialize one verdict."""

    rule_id: str
    week: int | str
    priority: str


def parse_roadmap_profile(
    options: dict[str, object] | None,
) -> RoadmapProfile | None:
    """Return a validated roadmap profile, or None when roadmap is disabled."""

    if not options or options.get("rule_profile") is None:
        return None

    raw_profile = options["rule_profile"]
    if not isinstance(raw_profile, dict):
        raise ValueError("review job rule_profile option must be an object or null")

    profile_id = raw_profile.get("id")
    if profile_id != ROADMAP_PROFILE_ID:
        raise ValueError(f"Unsupported roadmap profile: {profile_id!r}")

    raw_weeks = raw_profile.get("weeks_included")
    if raw_weeks is None:
        return RoadmapProfile(profile_id=profile_id, weeks_included=None)
    if not isinstance(raw_weeks, list) or any(
        not isinstance(week, int) or isinstance(week, bool) for week in raw_weeks
    ):
        raise ValueError("rule_profile.weeks_included must be a list of integers")

    return RoadmapProfile(
        profile_id=profile_id,
        weeks_included=tuple(sorted(set(raw_weeks))),
    )


def get_applicable_rule_ids(
    profile: RoadmapProfile,
    *,
    vectorstore: KnowledgeDocumentStore | None = None,
) -> list[str]:
    """Return every applicable rule id without semantic top-k retrieval."""

    return [
        metadata.rule_id
        for metadata in get_applicable_rule_metadata(
            profile,
            vectorstore=vectorstore,
        )
    ]


def get_applicable_rule_metadata(
    profile: RoadmapProfile,
    *,
    vectorstore: KnowledgeDocumentStore | None = None,
) -> list[RoadmapRuleMetadata]:
    """Return metadata for every applicable roadmap knowledge document."""

    selected_rules: list[tuple[int, RoadmapRuleMetadata]] = []
    selected_weeks = (
        set(profile.weeks_included) if profile.weeks_included is not None else None
    )
    store = vectorstore or get_vectorstore()
    for document in store.all_documents():
        metadata = document.metadata
        if metadata.get("doc_type") != "roadmap_rule":
            continue
        if metadata.get("profile_id") != profile.profile_id:
            continue

        week = metadata.get("week")
        if week != "GEN" and selected_weeks is not None and week not in selected_weeks:
            continue

        rule_id = metadata.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            continue
        priority = metadata.get("priority")
        if priority not in {"P0", "P1", "P2"}:
            raise ValueError(f"Roadmap rule {rule_id} has invalid priority")
        if not isinstance(week, int | str) or isinstance(week, bool):
            raise ValueError(f"Roadmap rule {rule_id} has invalid week")
        selected_rules.append(
            (
                _week_sort_key(week),
                RoadmapRuleMetadata(
                    rule_id=rule_id,
                    week=week,
                    priority=priority,
                ),
            )
        )

    selected_rules.sort(key=lambda item: (item[0], item[1].rule_id))
    rule_metadata = [metadata for _week, metadata in selected_rules]
    rule_ids = [metadata.rule_id for metadata in rule_metadata]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("knowledge_base contains duplicate roadmap rule ids")
    if not rule_metadata:
        raise ValueError(
            f"No roadmap rules found in knowledge_base for {profile.profile_id}"
        )

    return rule_metadata


def build_roadmap_context(
    options: dict[str, object] | None,
    *,
    vectorstore: KnowledgeDocumentStore | None = None,
) -> dict[str, object] | None:
    """Build compact roadmap context for analyze_project_structure."""

    profile = parse_roadmap_profile(options)
    if profile is None:
        return None

    return {
        "profile_id": profile.profile_id,
        "weeks_included": (
            list(profile.weeks_included) if profile.weeks_included is not None else None
        ),
        "applicable_rule_ids": get_applicable_rule_ids(
            profile,
            vectorstore=vectorstore,
        ),
        "review_rules": _review_rules(profile),
        "ai_verification_rules": _ai_verification_rules(profile),
    }


def _review_rules(profile: RoadmapProfile) -> list[dict[str, object]]:
    return [
        _roadmap_rule_context(requirement)
        for requirement in _requirements_for_profile(profile)
    ]


def _ai_verification_rules(profile: RoadmapProfile) -> list[dict[str, object]]:
    return [
        _roadmap_rule_context(requirement)
        for requirement in _requirements_for_profile(profile)
        if requirement.needs_ai_verification
    ]


def _requirements_for_profile(profile: RoadmapProfile) -> list[RoadmapRequirement]:
    selected_weeks = (
        set(profile.weeks_included) if profile.weeks_included is not None else None
    )
    requirements: list[RoadmapRequirement] = []
    for requirement in load_roadmap_requirements():
        if (
            requirement.week != "GEN"
            and selected_weeks is not None
            and requirement.week not in selected_weeks
        ):
            continue

        requirements.append(requirement)

    return sorted(
        requirements,
        key=lambda requirement: (_week_sort_key(requirement.week), requirement.rule_id),
    )


def _roadmap_rule_context(requirement: RoadmapRequirement) -> dict[str, object]:
    return {
        "rule_id": requirement.rule_id,
        "week": requirement.week,
        "priority": requirement.priority,
        "skill_group": requirement.skill_group,
        "category": requirement.category,
        "review_category": _review_category(requirement),
        "check_type": requirement.check_type,
        "needs_ai_verification": requirement.needs_ai_verification,
        "requirement": requirement.requirement,
        "verification_hint": requirement.verification_hint,
    }


def _review_category(requirement: RoadmapRequirement) -> str:
    text = " ".join(
        value.lower()
        for value in (
            requirement.skill_group,
            requirement.requirement,
            requirement.verification_hint or "",
            requirement.check_type or "",
        )
    )
    if any(term in text for term in ("jwt", "auth", "password", "token", "secret")):
        return "security"
    if any(term in text for term in ("cache", "ttl", "redis", "performance")):
        return "performance"
    if any(term in text for term in ("websocket", "sse", "pub/sub", "realtime")):
        return "realtime"
    if any(term in text for term in ("rag", "llm", "memory", "retriev", "chatbot")):
        return "ai"
    if requirement.check_type in {
        "required_dependency",
        "required_folder",
        "required_file",
        "required_config_key",
        "min_file_count",
    }:
        return "structure"
    return "requirement"


def _week_sort_key(week: object) -> int:
    if isinstance(week, int) and not isinstance(week, bool):
        return week
    if week == "GEN":
        return 10_000
    return 20_000
