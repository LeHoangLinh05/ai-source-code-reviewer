"""Fix job model definitions for generated repository patches."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base
from app.models.repository import RepositoryPlatform

if TYPE_CHECKING:
    from app.models.fix_audit_log import FixAuditLog
    from app.models.review_job import ReviewJob
    from app.models.user import User


class FixJobStatus(StrEnum):
    """Workflow states used by code-fix jobs."""

    PENDING = "PENDING"
    PREPARING = "PREPARING"
    GENERATING_PATCH = "GENERATING_PATCH"
    VALIDATING = "VALIDATING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class FixValidationStatus(StrEnum):
    """Validation summary states for a generated patch."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


class FixPublishStatus(StrEnum):
    """Publish states for turning an approved patch into a pull request."""

    NOT_REQUESTED = "NOT_REQUESTED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    NEEDS_FORK = "NEEDS_FORK"
    STALE_BASE = "STALE_BASE"


class FixJob(Base):
    """Background job that generates a patch for selected review issues."""

    __tablename__ = "fix_jobs"
    __table_args__ = (
        Index("idx_fix_jobs_review_job", "review_job_id"),
        Index("idx_fix_jobs_user", "user_id"),
        Index("idx_fix_jobs_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    review_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[FixJobStatus] = mapped_column(
        Enum(
            FixJobStatus,
            name="fix_job_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=FixJobStatus.PENDING,
        server_default=FixJobStatus.PENDING.value,
        nullable=False,
    )
    validation_status: Mapped[FixValidationStatus] = mapped_column(
        Enum(
            FixValidationStatus,
            name="fix_validation_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=FixValidationStatus.NOT_RUN,
        server_default=FixValidationStatus.NOT_RUN.value,
        nullable=False,
    )
    issue_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_branch: Mapped[str] = mapped_column(String(100), nullable=False)
    base_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    fix_branch: Mapped[str] = mapped_column(String(150), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    sandbox_path: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[str | None] = mapped_column(Text)
    changed_files: Mapped[list[str] | None] = mapped_column(JSONB)
    issue_plan: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
        nullable=False,
    )
    issue_results: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
        nullable=False,
    )
    validation_output: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    pr_url: Mapped[str | None] = mapped_column(Text)
    publish_status: Mapped[FixPublishStatus] = mapped_column(
        Enum(
            FixPublishStatus,
            name="fix_publish_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=FixPublishStatus.NOT_REQUESTED,
        server_default=FixPublishStatus.NOT_REQUESTED.value,
        nullable=False,
    )
    publish_error: Mapped[str | None] = mapped_column(Text)
    publish_strategy: Mapped[str] = mapped_column(
        String(20),
        default="fork",
        server_default="fork",
        nullable=False,
    )
    publish_allow_failed_validation: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    publish_override_reason: Mapped[str | None] = mapped_column(Text)
    published_branch: Mapped[str | None] = mapped_column(String(150))
    published_commit_sha: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[RepositoryPlatform | None] = mapped_column(
        Enum(
            RepositoryPlatform,
            name="repository_platform",
            values_callable=lambda platforms: [
                platform.value for platform in platforms
            ],
        ),
    )
    publish_sandbox_path: Mapped[str | None] = mapped_column(Text)
    publish_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    fork_repository_full_name: Mapped[str | None] = mapped_column(String(255))
    fork_branch: Mapped[str | None] = mapped_column(String(150))
    upstream_repository_full_name: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    review_job: Mapped[ReviewJob] = relationship(back_populates="fix_jobs")
    user: Mapped[User] = relationship(back_populates="fix_jobs")
    audit_logs: Mapped[list[FixAuditLog]] = relationship(
        back_populates="fix_job",
        cascade="all, delete-orphan",
        order_by="FixAuditLog.created_at",
    )
