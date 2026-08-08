"""Provider installation records for connected source-control accounts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.models.base import Base
from app.models.repository import RepositoryPlatform

if TYPE_CHECKING:
    from app.models.user import User


class ProviderInstallation(Base):
    """Git provider installation connected by a RepoGuard user."""

    __tablename__ = "provider_installations"
    __table_args__ = (
        Index("idx_provider_installations_user", "user_id"),
        Index("idx_provider_installations_provider", "provider"),
        UniqueConstraint(
            "user_id",
            "provider",
            "installation_id",
            name="uq_provider_installations_user_provider_installation",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[RepositoryPlatform] = mapped_column(
        Enum(
            RepositoryPlatform,
            name="repository_platform",
            values_callable=lambda platforms: [
                platform.value for platform in platforms
            ],
        ),
        nullable=False,
    )
    installation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(50))
    repository_selection: Mapped[str | None] = mapped_column(String(50))
    permissions: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    user: Mapped[User] = relationship(back_populates="provider_installations")
