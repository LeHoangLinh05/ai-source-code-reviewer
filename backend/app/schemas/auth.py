"""Authentication request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    """Payload for creating a new user account."""

    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        """Normalize and minimally validate email addresses."""

        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise ValueError("email must contain @")
        return normalized_email


class LoginRequest(BaseModel):
    """Payload for exchanging credentials for JWT tokens."""

    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        """Normalize email before credential lookup."""

        return email.strip().lower()


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


class AccessTokenResponse(BaseModel):
    """Public auth response returned when refresh token is stored in a cookie."""

    access_token: str
    user: UserResponse


class LogoutResponse(BaseModel):
    """Response returned after blacklisting the current access token."""

    message: str
