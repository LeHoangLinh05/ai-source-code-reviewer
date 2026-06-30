"""Repository model definitions for tracked source repositories."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.review_job import ReviewJob
    from app.models.user import User


class RepositoryPlatform(StrEnum):
    """Supported source hosting platforms."""

    GITHUB = "github"
    GITLAB = "gitlab"
    OTHER = "other"


class Repository(Base):
    """Source repository registered by a user for review jobs."""

    __tablename__ = "repositories"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[RepositoryPlatform | None] = mapped_column(
        Enum(
            RepositoryPlatform,
            name="repository_platform",
            values_callable=lambda platforms: [
                platform.value for platform in platforms
            ],
        ),
    )
    default_branch: Mapped[str] = mapped_column(
        String(100),
        default="main",
        server_default="main",
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    user: Mapped[User] = relationship(back_populates="repositories")
    review_jobs: Mapped[list[ReviewJob]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )
