"""Typed internal contracts for planning, generating, and verifying fixes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.review_issue import ReviewIssue
from app.schemas.fix_job import FixIssuePlan, FixIssueResult, FixScenarioResult


class FixIssueSpec(BaseModel):
    """Normalized finding and bounded source context used by the fix pipeline."""

    issue_id: UUID
    probe_id: str | None = None
    judge_question: str | None = None
    file_path: str
    line_start: int
    line_end: int
    severity: str
    category: str
    source: str
    rule_id: str | None = None
    title: str
    description: str
    suggestion: str | None = None
    supporting_evidence: list[dict[str, object]] = Field(default_factory=list)
    source_files: dict[str, str] = Field(default_factory=dict)
    context_truncated: bool = False


class FixPlanningResponse(BaseModel):
    """Root response returned by the issue planner."""

    plans: list[FixIssuePlan] = Field(default_factory=list)


class FixGenerationDispositionStatus(StrEnum):
    """Generation outcome before semantic verification."""

    CHANGED = "changed"
    NOT_CHANGED = "not_changed"
    UNCERTAIN = "uncertain"


class FixGenerationDisposition(BaseModel):
    """One generation disposition for one planned issue."""

    issue_id: UUID
    status: FixGenerationDispositionStatus
    summary: str


class FixUpdatedFile(BaseModel):
    """Complete replacement content for one planned editable file."""

    path: str
    updated_content: str


class FixGenerationResponse(BaseModel):
    """One coherent multi-file patch response."""

    files: list[FixUpdatedFile] = Field(default_factory=list)
    dispositions: list[FixGenerationDisposition] = Field(default_factory=list)


class FixVerificationResponse(BaseModel):
    """Root response returned by the semantic fallback verifier."""

    results: list[FixIssueResult] = Field(default_factory=list)


class FixTestFramework(StrEnum):
    """Supported runners for temporary issue verification tests."""

    PYTEST = "pytest"
    VITEST = "vitest"
    JEST = "jest"


class FixEnvironmentStatus(StrEnum):
    """Dependency preparation state for a target project."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FixGeneratedTest(BaseModel):
    """LLM-produced test source bound to one pre-approved scenario."""

    issue_id: UUID
    scenario_id: str
    content: str


class FixTestGenerationResponse(BaseModel):
    """Strict response returned by the independent test generator."""

    tests: list[FixGeneratedTest] = Field(default_factory=list)


@dataclass(slots=True, frozen=True)
class FixTestArtifact:
    """Temporary on-disk test with immutable source and runner metadata."""

    issue_id: UUID
    scenario_id: str
    framework: FixTestFramework
    project_root: Path
    file_path: Path
    content: str


@dataclass(slots=True, frozen=True)
class FixProjectEnvironment:
    """Prepared, allowlisted command prefix for one target project."""

    project_root: Path
    framework: FixTestFramework | None
    status: FixEnvironmentStatus
    runner_command: tuple[str, ...] = ()
    reason: str = ""


@dataclass(slots=True)
class FixVerificationContext:
    """In-memory baseline state that must never be persisted as source code."""

    artifacts: list[FixTestArtifact] = field(default_factory=list)
    baseline_results: dict[UUID, list[FixScenarioResult]] = field(default_factory=dict)
    related_tests: dict[UUID, list[Path]] = field(default_factory=dict)
    project_environments: dict[Path, FixProjectEnvironment] = field(
        default_factory=dict
    )


def get_probe_review(issue: ReviewIssue) -> dict[str, object]:
    """Return normalized probe metadata from a persisted review issue."""

    raw_output = issue.raw_output or {}
    probe_review = raw_output.get("probe_review")
    return probe_review if isinstance(probe_review, dict) else {}


def get_probe_id(issue: ReviewIssue) -> str | None:
    """Return the semantic probe identifier attached to a finding."""

    value = get_probe_review(issue).get("probe_id")
    return value.strip() if isinstance(value, str) and value.strip() else None
