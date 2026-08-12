"""Append-only audit log entries for fix and publish workflows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.fix_job import FixJob
    from app.models.user import User


class FixAuditAction(StrEnum):
    """Stable audit event names for fix generation and publishing."""

    FIX_GENERATED = "FIX_GENERATED"
    DIFF_VIEWED = "DIFF_VIEWED"
    PUBLISH_APPROVED = "PUBLISH_APPROVED"
    BRANCH_PUSHED = "BRANCH_PUSHED"
    PR_CREATED = "PR_CREATED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISH_RETRIED = "PUBLISH_RETRIED"
    PUBLISH_CANCELED = "PUBLISH_CANCELED"
    NEEDS_FORK = "NEEDS_FORK"
    STALE_BASE = "STALE_BASE"


class FixAuditLog(Base):
    """One audit event tied to a generated fix job."""

    __tablename__ = "fix_audit_logs"
    __table_args__ = (
        Index("idx_fix_audit_logs_fix_job_created", "fix_job_id", "created_at"),
        Index("idx_fix_audit_logs_user", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    fix_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fix_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    action: Mapped[FixAuditAction] = mapped_column(
        Enum(
            FixAuditAction,
            name="fix_audit_action",
            values_callable=lambda actions: [action.value for action in actions],
        ),
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    fix_job: Mapped[FixJob] = relationship(back_populates="audit_logs")
    user: Mapped[User | None] = relationship(back_populates="fix_audit_logs")
