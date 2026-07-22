"""Authentication API routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Response, status

from app.core.config import Settings
from app.core.dependencies import (
    AccessTokenDep,
    AuthServiceDep,
    CurrentUserDep,
    RefreshTokenCookieDep,
    SettingsDep,
)
from app.core.exceptions import AuthenticationError
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPairResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
ACCESS_COOKIE_PATH = "/api"
REFRESH_COOKIE_PATH = "/api/auth"
LEGACY_ACCESS_COOKIE_NAME = "auth_token"
LEGACY_ACCESS_COOKIE_PATH = "/"
LEGACY_ACCESS_COOKIE_SECURE = True
LEGACY_ACCESS_COOKIE_SAMESITE: Literal["lax"] = "lax"
SECONDS_PER_MINUTE = 60
SECONDS_PER_DAY = 24 * 60 * 60
RefreshTokenPayloadBody = Annotated[RefreshTokenRequest | None, Body()]
LogoutPayloadBody = Annotated[LogoutRequest | None, Body()]


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a user account",
)
async def register(
    payload: RegisterRequest,
    auth_service: AuthServiceDep,
) -> RegisterResponse:
    """Create a user account without starting an authenticated session."""

    user = await auth_service.register(payload)
    return RegisterResponse(message="Account created", user=user)


@router.post(
    "/login",
    response_model=TokenPairResponse,
    summary="Login with email and password",
)
async def login(
    payload: LoginRequest,
    response: Response,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> TokenPairResponse:
    """Authenticate credentials and return a new token pair."""

    token_pair = await auth_service.login(payload)
    _set_access_cookie(response, token_pair.access_token, settings)
    _set_refresh_cookie(response, token_pair.refresh_token, settings)
    _delete_legacy_access_cookie(response)
    return token_pair


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    summary="Refresh JWT tokens",
)
async def refresh(
    response: Response,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
    payload: RefreshTokenPayloadBody = None,
    refresh_token_cookie: RefreshTokenCookieDep = None,
) -> TokenPairResponse:
    """Rotate a valid refresh token into a new access/refresh pair."""

    refresh_token = (
        payload.refresh_token if payload is not None else refresh_token_cookie
    )
    if refresh_token is None:
        raise AuthenticationError("Refresh token is required")

    token_pair = await auth_service.refresh(refresh_token)
    _set_access_cookie(response, token_pair.access_token, settings)
    _set_refresh_cookie(response, token_pair.refresh_token, settings)
    _delete_legacy_access_cookie(response)
    return token_pair


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Logout current session",
)
async def logout(
    response: Response,
    access_token: AccessTokenDep,
    current_user: CurrentUserDep,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
    payload: LogoutPayloadBody = None,
    refresh_token_cookie: RefreshTokenCookieDep = None,
) -> LogoutResponse:
    """Blacklist the current access token until it expires."""

    refresh_token = (
        payload.refresh_token if payload is not None else refresh_token_cookie
    )
    await auth_service.logout(access_token, refresh_token)
    _delete_access_cookie(response, settings)
    _delete_refresh_cookie(response, settings)
    _delete_legacy_access_cookie(response)
    return LogoutResponse(message=f"User {current_user.email} logged out")


@router.post(
    "/logout-all",
    response_model=LogoutResponse,
    summary="Logout all sessions",
)
async def logout_all(
    response: Response,
    access_token: AccessTokenDep,
    current_user: CurrentUserDep,
    auth_service: AuthServiceDep,
    settings: SettingsDep,
) -> LogoutResponse:
    """Revoke every refresh token issued for the current user."""

    await auth_service.logout_all(access_token, current_user)
    _delete_access_cookie(response, settings)
    _delete_refresh_cookie(response, settings)
    _delete_legacy_access_cookie(response)
    return LogoutResponse(message=f"All sessions for {current_user.email} logged out")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
)
async def get_me(
    current_user: CurrentUserDep,
) -> User:
    """Return the profile attached to the current valid access token."""

    return current_user


def _set_access_cookie(
    response: Response,
    access_token: str,
    settings: Settings,
) -> None:
    max_age = settings.jwt_access_token_expire_minutes * SECONDS_PER_MINUTE
    response.set_cookie(
        key=settings.access_cookie_name,
        value=access_token,
        max_age=max_age,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _set_refresh_cookie(
    response: Response,
    refresh_token: str,
    settings: Settings,
) -> None:
    max_age = settings.jwt_refresh_token_expire_days * SECONDS_PER_DAY
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _delete_access_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.access_cookie_name,
        path=ACCESS_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _delete_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _delete_legacy_access_cookie(response: Response) -> None:
    response.delete_cookie(
        key=LEGACY_ACCESS_COOKIE_NAME,
        path=LEGACY_ACCESS_COOKIE_PATH,
        httponly=True,
        secure=LEGACY_ACCESS_COOKIE_SECURE,
        samesite=LEGACY_ACCESS_COOKIE_SAMESITE,
    )
