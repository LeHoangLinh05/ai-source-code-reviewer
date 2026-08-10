"""Typed contracts shared by probe planning, retrieval, and judging."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAX_RETRIEVAL_QUERY_WORDS = 64
SENSITIVE_DATA_LOGGING_PROBE_ID = "security.sensitive_data_logging"
UNRESTRICTED_FILE_UPLOAD_PROBE_ID = "security.unrestricted_file_upload"


class ProbeLane(StrEnum):
    """Independent review budgets used by the directed probe pipeline."""

    DEFECT = "defect"
    COVERAGE = "coverage"
    ROADMAP = "roadmap"


@dataclass(frozen=True, slots=True)
class ProbeDefinition:
    """One focused claim with separate retrieval and judging instructions."""

    probe_id: str
    lane: ProbeLane
    category: str
    priority: str
    risk_area: str
    retrieval_queries: tuple[str, ...]
    lexical_terms: tuple[str, ...]
    judge_question: str
    top_k: int
    file_scope: str | None = None
    related_rule_ids: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ("baseline",)
    reason: str = "category_probe"
    probe_kind: str = "baseline"

    def __post_init__(self) -> None:
        if not self.probe_id.strip():
            raise ValueError("probe_id is required")
        if not self.retrieval_queries:
            raise ValueError("at least one retrieval query is required")
        if any(
            len(query.split()) > MAX_RETRIEVAL_QUERY_WORDS
            for query in self.retrieval_queries
        ):
            raise ValueError(
                f"retrieval queries must not exceed {MAX_RETRIEVAL_QUERY_WORDS} words"
            )
        if self.top_k <= 0:
            raise ValueError("top_k must be greater than zero")

    @property
    def primary_query(self) -> str:
        """Return the first short query used by lexical compatibility paths."""

        return self.retrieval_queries[0] if self.retrieval_queries else ""

    def prompt_payload(self) -> dict[str, object]:
        """Serialize the probe fields that the evidence judge needs."""

        return {
            "probe_id": self.probe_id,
            "lane": self.lane.value,
            "category": self.category,
            "priority": self.priority,
            "risk_area": self.risk_area,
            "judge_question": self.judge_question,
            "related_rule_ids": list(self.related_rule_ids),
            "file_scope": self.file_scope,
        }
