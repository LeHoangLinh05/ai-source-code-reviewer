"""Tests for authentication route contracts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Response

from app.core.dependencies import get_current_admin
from app.core.exceptions import AuthorizationError
from app.models.user import User, UserRole
from app.routers.admin import get_admin_me
from app.routers.auth import login, refresh, register
from app.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

VALID_EMAIL = "user@example.com"
VALID_PASSWORD = "Valid@123"


@pytest.mark.asyncio
async def test_register_route_returns_user_without_auth_cookies() -> None:
    user = build_user()
    response = Response()
    result = await register(
        RegisterRequest(email=VALID_EMAIL, password=VALID_PASSWORD),
        cast(AuthService, FakeAuthService(user=user)),
    )

    assert result.message == "Account created"
    assert result.user.email == VALID_EMAIL
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_login_route_returns_token_pair_and_sets_cookies() -> None:
    user = build_user()
    response = Response()

    result = await login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD),
        response,
        cast(AuthService, FakeAuthService(user=user)),
        build_settings(),
    )

    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert result.token_type == "Bearer"
    set_cookie_headers = set_cookie_values(response)
    assert any("accessToken=access-token" in header for header in set_cookie_headers)
    assert any("refreshToken=refresh-token" in header for header in set_cookie_headers)
    assert all("HttpOnly" in header for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_refresh_route_accepts_body_token_and_rotates_cookies() -> None:
    user = build_user()
    response = Response()

    result = await refresh(
        response,
        cast(AuthService, FakeAuthService(user=user)),
        build_settings(),
        RefreshTokenRequest(refresh_token="old-refresh-token"),
    )

    assert result.access_token == "access-token"
    assert result.refresh_token == "refresh-token"
    assert any(
        "refreshToken=refresh-token" in header for header in set_cookie_values(response)
    )


@pytest.mark.asyncio
async def test_admin_me_route_returns_current_admin() -> None:
    admin = build_user(role=UserRole.ADMIN)

    result = await get_admin_me(admin)

    assert result is admin


@pytest.mark.asyncio
async def test_current_admin_dependency_rejects_regular_user() -> None:
    user = build_user(role=UserRole.USER)

    with pytest.raises(AuthorizationError, match="Admin role is required"):
        await get_current_admin(user)


def build_user(role: UserRole = UserRole.USER) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        email=VALID_EMAIL,
        hashed_password="hashed-password",
        full_name=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def build_settings():
    return SimpleNamespace(
        access_cookie_name="accessToken",
        refresh_cookie_name="refreshToken",
        refresh_cookie_secure=False,
        refresh_cookie_samesite="lax",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=7,
    )


def set_cookie_values(response: Response) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in response.raw_headers
        if key == b"set-cookie"
    ]


class FakeAuthService:
    def __init__(self, user: User) -> None:
        self.user = user

    async def register(self, _payload: RegisterRequest) -> UserResponse:
        return UserResponse.model_validate(self.user)

    async def login(self, _payload: LoginRequest) -> TokenPairResponse:
        return self._build_token_pair()

    async def refresh(self, _refresh_token: str) -> TokenPairResponse:
        return self._build_token_pair()

    def _build_token_pair(self) -> TokenPairResponse:
        return TokenPairResponse(
            access_token="access-token",
            refresh_token="refresh-token",
            user=UserResponse.model_validate(self.user),
        )
