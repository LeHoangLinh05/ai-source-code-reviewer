"""Review job model definitions for code review workflow state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.job_status_history import JobStatusHistory
    from app.models.repository import Repository
    from app.models.review_issue import ReviewIssue
    from app.models.review_report import ReviewReport
    from app.models.user import User


class ReviewJobStatus(StrEnum):
    """Workflow states used by repository review jobs."""

    PENDING = "PENDING"
    CLONING = "CLONING"
    ANALYZING_STRUCTURE = "ANALYZING_STRUCTURE"
    RUNNING_STATIC_ANALYSIS = "RUNNING_STATIC_ANALYSIS"
    CHUNKING_CODE = "CHUNKING_CODE"
    AI_REVIEWING = "AI_REVIEWING"
    GENERATING_REPORT = "GENERATING_REPORT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReviewJob(Base):
    """Background review job created for a repository and user."""

    __tablename__ = "review_jobs"
    __table_args__ = (
        Index("idx_review_jobs_user", "user_id"),
        Index("idx_review_jobs_status", "status"),
        Index("idx_review_jobs_repository", "repository_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    repository_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ReviewJobStatus] = mapped_column(
        Enum(
            ReviewJobStatus,
            name="review_job_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=ReviewJobStatus.PENDING,
        server_default=ReviewJobStatus.PENDING.value,
        nullable=False,
    )
    branch: Mapped[str | None] = mapped_column(String(100))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    sandbox_path: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    repository: Mapped[Repository] = relationship(back_populates="review_jobs")
    user: Mapped[User] = relationship(back_populates="review_jobs")
    report: Mapped[ReviewReport | None] = relationship(
        back_populates="review_job",
        cascade="all, delete-orphan",
        uselist=False,
    )
    issues: Mapped[list[ReviewIssue]] = relationship(
        back_populates="review_job",
        cascade="all, delete-orphan",
    )
    status_history: Mapped[list[JobStatusHistory]] = relationship(
        back_populates="review_job",
        cascade="all, delete-orphan",
        order_by="JobStatusHistory.changed_at",
    )
