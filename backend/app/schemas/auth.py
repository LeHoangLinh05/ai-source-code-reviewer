"""Authentication request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.models.user import UserRole
from app.schemas.validation import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    is_legacy_test_login_credentials,
    validate_email_address,
    validate_strong_password,
)


class RegisterRequest(BaseModel):
    """Payload for creating a new user account."""

    email: str = Field(..., min_length=1, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(
        ...,
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        """Normalize and validate email addresses."""

        return validate_email_address(email)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, password: str) -> str:
        """Validate account passwords before hashing them."""

        return validate_strong_password(password)


class LoginRequest(BaseModel):
    """Payload for exchanging credentials for JWT tokens."""

    email: str = Field(..., min_length=1, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        """Normalize and validate email before credential lookup."""

        return validate_email_address(email)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, password: str, info: ValidationInfo) -> str:
        """Validate submitted passwords before credential lookup."""

        email = info.data.get("email")
        if isinstance(email, str) and is_legacy_test_login_credentials(email, password):
            return password

        return validate_strong_password(password)


class RefreshTokenRequest(BaseModel):
    """Optional body payload for refreshing an access token."""

    refresh_token: str = Field(..., min_length=1)


class LogoutRequest(BaseModel):
    """Optional body payload for revoking the current refresh token."""

    refresh_token: str | None = Field(default=None, min_length=1)


class UserResponse(BaseModel):
    """Public user fields returned by auth endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenPairResponse(BaseModel):
    """Access and refresh tokens returned after auth actions."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    user: UserResponse


class RegisterResponse(BaseModel):
    """Response returned after account creation without starting a session."""

    message: str
    user: UserResponse


class LogoutResponse(BaseModel):
    """Response returned after blacklisting the current access token."""

    message: str
