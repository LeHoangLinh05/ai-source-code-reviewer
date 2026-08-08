"""Fix job request, response, validation, and progress schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.models.fix_audit_log import FixAuditAction
from app.models.fix_job import FixJobStatus, FixPublishStatus, FixValidationStatus
from app.models.repository import RepositoryPlatform


class FixValidationCheckStatus(StrEnum):
    """Per-command validation result values."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FixValidationCheck(BaseModel):
    """One validation command run against a generated patch."""

    name: str
    command: str
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
    validation_output: dict[str, object] | list[dict[str, object]] | None
    validation_summary: FixValidationResult | None
    publish_status: FixPublishStatus
    publish_error: str | None
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
