"""Authentication API routes."""

from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends, Response, status

from app.core.config import get_settings
from app.core.dependencies import (
    get_auth_service,
    get_current_access_token,
    get_current_user,
)
from app.core.exceptions import AuthenticationError
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenPairResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a user account",
)
async def register(
    payload: RegisterRequest,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPairResponse:
    """Create a user account and return access and refresh tokens."""

    token_pair = await auth_service.register(payload)
    _set_refresh_cookie(response, token_pair.refresh_token)
    return token_pair


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    summary="Login with email and password",
)
async def login(
    payload: LoginRequest,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AccessTokenResponse:
    """Authenticate credentials and return a new token pair."""

    token_pair = await auth_service.login(payload)
    _set_refresh_cookie(response, token_pair.refresh_token)
    _delete_legacy_access_cookie(response)
    return AccessTokenResponse(
        access_token=token_pair.access_token,
        user=token_pair.user,
    )


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Refresh JWT tokens",
)
async def refresh(
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    payload: Annotated[RefreshTokenRequest | None, Body()] = None,
    refresh_token_cookie: Annotated[str | None, Cookie(alias="refreshToken")] = None,
) -> AccessTokenResponse:
    """Rotate a valid refresh token into a new access/refresh pair."""

    refresh_token = (
        payload.refresh_token if payload is not None else refresh_token_cookie
    )
    if refresh_token is None:
        raise AuthenticationError("Refresh token is required")

    token_pair = await auth_service.refresh(refresh_token)
    _set_refresh_cookie(response, token_pair.refresh_token)
    _delete_legacy_access_cookie(response)
    return AccessTokenResponse(
        access_token=token_pair.access_token,
        user=token_pair.user,
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Logout current session",
)
async def logout(
    response: Response,
    access_token: Annotated[str, Depends(get_current_access_token)],
    current_user: Annotated[User, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    payload: Annotated[LogoutRequest | None, Body()] = None,
    refresh_token_cookie: Annotated[str | None, Cookie(alias="refreshToken")] = None,
) -> LogoutResponse:
    """Blacklist the current access token until it expires."""

    refresh_token = (
        payload.refresh_token if payload is not None else refresh_token_cookie
    )
    await auth_service.logout(access_token, refresh_token)
    _delete_refresh_cookie(response)
    _delete_legacy_access_cookie(response)
    return LogoutResponse(message=f"User {current_user.email} logged out")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Return the profile attached to the current valid access token."""

    return current_user


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    max_age = settings.jwt_refresh_token_expire_days * 24 * 60 * 60
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        path="/api/auth",
        httponly=True,
        secure=True,
        samesite="lax",
    )


def _delete_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/api/auth",
        httponly=True,
        secure=True,
        samesite="lax",
    )


def _delete_legacy_access_cookie(response: Response) -> None:
    response.delete_cookie(
        key="auth_token",
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
