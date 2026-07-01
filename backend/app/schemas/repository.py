"""Repository request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.repository import RepositoryPlatform


class RepositoryCreate(BaseModel):
    """Payload for registering a source repository."""

    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2048)
    platform: RepositoryPlatform | None = None
    default_branch: str = Field(default="main", min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name", "url", "default_branch")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """Trim required text fields and reject blank values."""

        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("field cannot be blank")
        return stripped_value

    @field_validator("description")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text fields."""

        if value is None:
            return None

        stripped_value = value.strip()
        return stripped_value or None


class RepositoryResponse(BaseModel):
    """Tracked repository fields returned by API endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    url: str
    platform: RepositoryPlatform | None
    default_branch: str
    description: str | None
    last_reviewed_at: datetime | None
    created_at: datetime


class DeleteResponse(BaseModel):
    """Response returned after deleting a resource."""

    message: str
