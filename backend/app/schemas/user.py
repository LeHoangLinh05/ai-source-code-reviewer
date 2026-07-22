"""Schemas for authenticated user profile settings."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole
from app.schemas.validation import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    validate_strong_password,
)


class UserProfileResponse(BaseModel):
    """Public user profile fields returned by settings endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserProfileUpdateRequest(BaseModel):
    """Payload for editing optional user profile fields."""

    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, full_name: str | None) -> str | None:
        """Strip whitespace and store empty profile names as null."""

        if full_name is None:
            return None

        normalized_name = full_name.strip()
        return normalized_name or None


class ChangePasswordRequest(BaseModel):
    """Payload for changing the current user's password."""

    current_password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, new_password: str) -> str:
        """Validate replacement passwords before hashing them."""

        return validate_strong_password(new_password)


class ChangePasswordResponse(BaseModel):
    """Response returned after a password update succeeds."""

    message: str
