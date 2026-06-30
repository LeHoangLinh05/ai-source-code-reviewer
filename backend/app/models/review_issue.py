"""Review issue model definitions for normalized findings."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.review_job import ReviewJob


class IssueSeverity(StrEnum):
    """Severity levels used by normalized review issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IssueCategory(StrEnum):
    """Issue categories shown in reports and filters."""

    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"
    BUG = "bug"


class IssueSource(StrEnum):
    """Analysis sources that can produce normalized issues."""

    AI_REVIEW = "ai_review"
    RUFF = "ruff"
    BANDIT = "bandit"
    ESLINT = "eslint"


class ReviewIssue(Base):
    """Normalized finding attached to a review job."""

    __tablename__ = "review_issues"
    __table_args__ = (
        Index("idx_issues_job", "job_id"),
        Index("idx_issues_severity", "severity"),
        Index("idx_issues_category", "category"),
        Index("idx_issues_file", "file_path"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(
            IssueSeverity,
            name="issue_severity",
            values_callable=lambda severities: [
                severity.value for severity in severities
            ],
        ),
        nullable=False,
    )
    category: Mapped[IssueCategory] = mapped_column(
        Enum(
            IssueCategory,
            name="issue_category",
            values_callable=lambda categories: [
                category.value for category in categories
            ],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    source: Mapped[IssueSource] = mapped_column(
        Enum(
            IssueSource,
            name="issue_source",
            values_callable=lambda sources: [source.value for source in sources],
        ),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    raw_output: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    review_job: Mapped[ReviewJob] = relationship(back_populates="issues")
