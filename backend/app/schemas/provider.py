"""Provider connection API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.repository import RepositoryPlatform


class GitHubInstallUrlResponse(BaseModel):
    """GitHub App installation URL for the current deployment."""

    install_url: str


class GitHubInstallationSyncRequest(BaseModel):
    """Metadata stored after a user completes GitHub App installation."""

    installation_id: str = Field(min_length=1, max_length=80)
    account_login: str | None = Field(default=None, max_length=255)
    account_type: str | None = Field(default=None, max_length=50)
    repository_selection: str | None = Field(default=None, max_length=50)
    permissions: dict[str, object] | None = None

    @field_validator("installation_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Normalize required provider identifiers."""

        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("field cannot be blank")

        return stripped_value

    @field_validator("account_login")
    @classmethod
    def strip_account_login(cls, value: str | None) -> str | None:
        """Normalize optional provider identifiers."""

        if value is None:
            return None

        stripped_value = value.strip()
        return stripped_value or None

    @field_validator("account_type", "repository_selection")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional provider metadata."""

        if value is None:
            return None

        stripped_value = value.strip()
        return stripped_value or None


class ProviderConnectionResponse(BaseModel):
    """Stored provider connection visible to the current user."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    provider: RepositoryPlatform
    installation_id: str
    account_login: str
    account_type: str | None
    repository_selection: str | None
    permissions: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class RepositoryProviderStatus(BaseModel):
    """Provider readiness for publishing fixes from one tracked repository."""

    repository_id: UUID
    provider: RepositoryPlatform | None
    is_connected: bool
    can_publish: bool
    installation_id: str | None
    account_login: str | None
    message: str
