"""Fix job request, response, validation, and progress schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import FixJobStatus, FixPublishStatus, FixValidationStatus
from app.models.repository import RepositoryPlatform

MIN_OVERRIDE_REASON_LENGTH = 20
EVIDENCE_FILE_PATH_ALIASES = ("file", "path", "source_file")
EVIDENCE_RATIONALE_ALIASES = (
    "reason",
    "explanation",
    "description",
    "summary",
    "details",
    "observation",
    "proof",
    "evidence",
)


def _optional_contract_part(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _first_nonempty_alias(
    values: dict[object, object], aliases: tuple[str, ...]
) -> str:
    for alias in aliases:
        normalized_value = _optional_contract_part(values.get(alias))
        if normalized_value:
            return normalized_value
    return ""


class FixValidationCheckStatus(StrEnum):
    """Per-command validation result values."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FixValidationCheckKind(StrEnum):
    """Kinds of checks contributing to the final patch verdict."""

    COMMAND = "command"
    LINT = "lint"
    TEST = "test"
    SEMANTIC = "semantic"


class FixIssuePlanStatus(StrEnum):
    """Planner outcomes for one selected issue."""

    PLANNED = "planned"
    NOT_FIXABLE = "not_fixable"
    UNCERTAIN = "uncertain"


class FixIssueVerdict(StrEnum):
    """Post-patch verification verdicts for one selected issue."""

    FIXED = "fixed"
    UNRESOLVED = "unresolved"
    UNCERTAIN = "uncertain"


class FixScenarioKind(StrEnum):
    """Behavior dimensions that a generated fix must preserve or change."""

    EXPLOIT = "exploit"
    PRESERVED_BEHAVIOR = "preserved_behavior"
    RELATED_TEST = "related_test"


class FixScenarioStatus(StrEnum):
    """Execution state for one scenario in one repository revision."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"


class FixVerificationScenario(BaseModel):
    """Testable behavior contract produced independently from the patch."""

    scenario_id: str = Field(min_length=1, max_length=120)
    kind: FixScenarioKind
    description: str = Field(min_length=1, max_length=1000)
    related_files: list[str] = Field(default_factory=list)


class FixScenarioResult(BaseModel):
    """Baseline and patched outcomes for one verification scenario."""

    scenario_id: str
    kind: FixScenarioKind
    framework: str | None = None
    baseline_status: FixScenarioStatus = FixScenarioStatus.NOT_RUN
    patched_status: FixScenarioStatus = FixScenarioStatus.NOT_RUN
    output: str = ""


class FixEvidenceReference(BaseModel):
    """Bounded source evidence supporting a fix verdict."""

    file_path: str
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    rationale: str

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_aliases(cls, value: object) -> object:
        """Normalize common LLM aliases without inventing missing evidence."""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        if not _optional_contract_part(normalized.get("file_path")):
            file_path = _first_nonempty_alias(normalized, EVIDENCE_FILE_PATH_ALIASES)
            if file_path:
                normalized["file_path"] = file_path
        if not _optional_contract_part(normalized.get("rationale")):
            rationale = _first_nonempty_alias(normalized, EVIDENCE_RATIONALE_ALIASES)
            if rationale:
                normalized["rationale"] = rationale
        return normalized


class FixIssuePlan(BaseModel):
    """Cross-file plan and acceptance contract for one selected issue."""

    issue_id: UUID
    probe_id: str | None = None
    root_cause: str
    safety_property: str
    editable_files: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    affected_contracts: list[str] = Field(default_factory=list)
    exploit_scenarios: list[FixVerificationScenario] = Field(default_factory=list)
    preserved_behavior_scenarios: list[FixVerificationScenario] = Field(
        default_factory=list
    )
    acceptance_checks: list[str] = Field(default_factory=list)
    forbidden_shortcuts: list[str] = Field(default_factory=list)
    status: FixIssuePlanStatus
    reason: str | None = None

    @field_validator("affected_contracts", mode="before")
    @classmethod
    def normalize_affected_contracts(cls, value: object) -> object:
        """Accept the structured contract form commonly returned by LLMs."""

        if not isinstance(value, list):
            return value

        normalized_contracts: list[object] = []
        for contract in value:
            if not isinstance(contract, dict):
                normalized_contracts.append(contract)
                continue

            contract_id = contract.get("contract_id")
            if not isinstance(contract_id, str) or not contract_id.strip():
                normalized_contracts.append(contract)
                continue

            method = _optional_contract_part(contract.get("method"))
            path = _optional_contract_part(contract.get("path"))
            description = _optional_contract_part(contract.get("description"))
            endpoint = " ".join(part for part in (method, path) if part)
            normalized = contract_id.strip()
            if endpoint:
                normalized = f"{normalized} [{endpoint}]"
            if description:
                normalized = f"{normalized}: {description}"
            normalized_contracts.append(normalized)

        return normalized_contracts


class FixIssueResult(BaseModel):
    """Final verification result for one selected issue."""

    issue_id: UUID
    probe_id: str | None = None
    verdict: FixIssueVerdict
    summary: str
    planned_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verification_attempts: int = Field(default=1, ge=0)
    evidence: list[FixEvidenceReference] = Field(default_factory=list)
    scenario_results: list[FixScenarioResult] = Field(default_factory=list)


class FixValidationCheck(BaseModel):
    """One validation command run against a generated patch."""

    name: str
    command: str
    kind: FixValidationCheckKind = FixValidationCheckKind.COMMAND
    required: bool = True
    status: FixValidationCheckStatus
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(ge=0)


class FixValidationResult(BaseModel):
    """Typed validation summary returned for a fix job."""

    status: FixValidationStatus
    summary: str
    checks: list[FixValidationCheck] = Field(default_factory=list)


FixJobProgressEventType = Literal[
    "status_change",
    "progress_update",
    "log",
    "completed",
    "failed",
]


class FixJobCreate(BaseModel):
    """Payload for generating a patch from selected review issues."""

    issue_ids: list[UUID] = Field(min_length=1)
    target_branch: str | None = Field(default=None, max_length=100)

    @field_validator("issue_ids")
    @classmethod
    def require_unique_issue_ids(cls, value: list[UUID]) -> list[UUID]:
        """Reject duplicate issue IDs before queuing expensive work."""

        unique_ids = list(dict.fromkeys(value))
        if len(unique_ids) != len(value):
            raise ValueError("issue_ids must be unique")

        return value

    @field_validator("target_branch")
    @classmethod
    def strip_optional_target_branch(cls, value: str | None) -> str | None:
        """Normalize optional branch names."""

        if value is None:
            return None

        stripped_value = value.strip()
        return stripped_value or None


PublishStrategy = Literal["fork"]


class PublishFixPayload(BaseModel):
    """Payload for publishing a generated patch as a pull request."""

    strategy: PublishStrategy = "fork"
    allow_failed_validation: bool = False
    override_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_override_reason(self) -> "PublishFixPayload":
        """Require an auditable explanation for unverified publishing."""

        if not self.allow_failed_validation:
            self.override_reason = None
            return self

        reason = (self.override_reason or "").strip()
        if len(reason) < MIN_OVERRIDE_REASON_LENGTH:
            raise ValueError(
                "override_reason must contain at least 20 characters when "
                "allow_failed_validation is true"
            )
        self.override_reason = reason
        return self


class FixJobResponse(BaseModel):
    """Fix job fields returned by API endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_job_id: UUID
    user_id: UUID
    status: FixJobStatus
    validation_status: FixValidationStatus
    issue_ids: list[str]
    target_branch: str
    base_commit_sha: str
    fix_branch: str
    error_message: str | None
    failure_reason: str | None
    changed_files: list[str] | None
    issue_plan: list[FixIssuePlan] = Field(default_factory=list)
    issue_results: list[FixIssueResult] = Field(default_factory=list)
    validation_output: dict[str, object] | list[dict[str, object]] | None
    validation_summary: FixValidationResult | None
    publish_status: FixPublishStatus
    publish_error: str | None
    publish_override_reason: str | None
    published_branch: str | None
    published_commit_sha: str | None
    provider: RepositoryPlatform | None
    publish_started_at: datetime | None
    publish_completed_at: datetime | None
    fork_repository_full_name: str | None
    fork_branch: str | None
    upstream_repository_full_name: str | None
    pr_url: str | None
    stream_url: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class FixDiffResponse(BaseModel):
    """Unified diff returned for a generated fix job."""

    fix_id: UUID
    diff: str
    changed_files: list[str]


class FixAuditLogResponse(BaseModel):
    """One audit event returned for a fix job timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fix_job_id: UUID
    user_id: UUID | None
    action: FixAuditAction
    message: str | None
    event_metadata: dict[str, object] | None
    created_at: datetime


class FixJobProgressEvent(BaseModel):
    """One normalized progress event for a fix job SSE stream."""

    fix_id: UUID
    review_job_id: UUID
    event: FixJobProgressEventType
    status: FixJobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    timestamp: datetime
    data: dict[str, object] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Return whether the stream should close after this event."""

        publish_status = self.data.get("publish_status")
        if self.status == FixJobStatus.WAITING_APPROVAL and publish_status in {
            FixPublishStatus.PUBLISHING.value,
            FixPublishStatus.FAILED.value,
            FixPublishStatus.NEEDS_FORK.value,
            FixPublishStatus.STALE_BASE.value,
        }:
            return False

        return self.event in {"completed", "failed"}


def build_fix_validation_summary(
    status: FixValidationStatus,
    checks: list[FixValidationCheck],
) -> str:
    """Return a concise user-facing validation summary."""

    if not checks:
        return "No validation commands ran for this patch."

    passed_count = sum(
        check.status == FixValidationCheckStatus.PASSED for check in checks
    )
    failed_count = sum(
        check.status == FixValidationCheckStatus.FAILED for check in checks
    )
    skipped_count = sum(
        check.status == FixValidationCheckStatus.SKIPPED for check in checks
    )
    summary_parts = [
        f"{passed_count} passed",
        f"{failed_count} failed",
        f"{skipped_count} skipped",
    ]
    if status == FixValidationStatus.FAILED:
        return f"Validation failed: {', '.join(summary_parts)}."

    if status == FixValidationStatus.PASSED:
        return f"Validation passed: {', '.join(summary_parts)}."

    return f"Validation did not run fully: {', '.join(summary_parts)}."


def normalize_fix_validation_result(
    *,
    validation_status: FixValidationStatus,
    validation_output: object,
) -> FixValidationResult | None:
    """Return typed validation output from current or legacy JSON payloads."""

    if validation_output is None:
        return None

    if isinstance(validation_output, dict):
        try:
            return FixValidationResult.model_validate(validation_output)
        except ValidationError:
            return _build_validation_result_from_checks(
                validation_status,
                [],
            )

    if isinstance(validation_output, list):
        checks = [
            check
            for item in validation_output
            if (check := _parse_validation_check(item)) is not None
        ]
        return _build_validation_result_from_checks(validation_status, checks)

    return _build_validation_result_from_checks(validation_status, [])


def _build_validation_result_from_checks(
    validation_status: FixValidationStatus,
    checks: list[FixValidationCheck],
) -> FixValidationResult:
    return FixValidationResult(
        status=validation_status,
        summary=build_fix_validation_summary(validation_status, checks),
        checks=checks,
    )


def _parse_validation_check(value: object) -> FixValidationCheck | None:
    try:
        return FixValidationCheck.model_validate(value)
    except ValidationError:
        return None
