"""FastAPI dependency providers shared across routers."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import TokenType, decode_token
from app.db.postgres import get_async_session
from app.db.redis import get_redis_client
from app.models.user import User, UserRole
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.token_blacklist import TokenBlacklistService

bearer_scheme = HTTPBearer()


async def get_redis() -> Redis:
    """Provide Redis to services that need cache or blacklist state."""

    return get_redis_client()


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    """Build auth service with request-scoped DB and shared Redis clients."""

    user_repository = UserRepository(session)
    refresh_token_repository = RefreshTokenRepository(session)
    token_blacklist_service = TokenBlacklistService(redis_client)
    return AuthService(
        user_repository,
        refresh_token_repository,
        token_blacklist_service,
    )


async def get_current_access_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> str:
    """Extract the current bearer access token from Authorization header."""

    return credentials.credentials


async def get_current_user(
    token: Annotated[str, Depends(get_current_access_token)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:
    """Decode JWT, reject blacklisted tokens, and load the active user."""

    decoded_token = decode_token(token, TokenType.ACCESS)
    is_blacklisted = await auth_service.token_blacklist_service.is_blacklisted(
        decoded_token["token_id"]
    )
    is_user_token_invalid = (
        await auth_service.token_blacklist_service.is_user_token_invalid(
            decoded_token["subject"],
            decoded_token["issued_at"],
        )
    )
    if is_blacklisted or is_user_token_invalid:
        raise AuthenticationError("Access token has been revoked")

    return await auth_service.get_active_user(decoded_token["subject"])


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require an authenticated admin user for protected admin endpoints."""

    if current_user.role != UserRole.ADMIN:
        raise AuthorizationError("Admin role is required")

    return current_user
