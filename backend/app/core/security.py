"""Security helpers for password hashing and JWT token handling."""

import hmac
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import TypedDict, cast
from uuid import UUID, uuid4

from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenType(StrEnum):
    """JWT token categories accepted by the auth module."""

    ACCESS = "access"
    REFRESH = "refresh"


class DecodedToken(TypedDict):
    """Validated JWT claims used by auth dependencies and services."""

    subject: UUID
    token_type: TokenType
    issued_at: datetime
    expires_at: datetime
    token_id: str


def hash_password(password: str) -> str:
    """Hash a plaintext password before storing it in PostgreSQL."""

    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored password hash."""

    return pwd_context.verify(password, hashed_password)


def create_access_token(user_id: UUID) -> str:
    """Create a short-lived access token for API authorization."""

    settings = get_settings()
    expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    return _create_token(
        user_id=user_id, token_type=TokenType.ACCESS, expires_delta=expires_delta
    )


def create_refresh_token(user_id: UUID) -> str:
    """Create a long-lived refresh token for issuing new token pairs."""

    settings = get_settings()
    expires_delta = timedelta(days=settings.jwt_refresh_token_expire_days)
    return _create_token(
        user_id=user_id, token_type=TokenType.REFRESH, expires_delta=expires_delta
    )


def decode_token(token: str, expected_type: TokenType) -> DecodedToken:
    """Decode and validate a JWT with its required token type."""

    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as error:
        raise AuthenticationError("Token has expired") from error
    except JWTError as error:
        raise AuthenticationError("Invalid token") from error

    subject = payload.get("sub")
    token_type = payload.get("type")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    token_id = payload.get("jti")

    if not isinstance(subject, str) or not isinstance(token_type, str):
        raise AuthenticationError("Invalid token claims")

    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise AuthenticationError("Invalid token claims")

    if not isinstance(token_id, str):
        raise AuthenticationError("Invalid token claims")

    if token_type != expected_type.value:
        raise AuthenticationError("Invalid token type")

    try:
        user_id = UUID(subject)
    except ValueError as error:
        raise AuthenticationError("Invalid token subject") from error

    return DecodedToken(
        subject=user_id,
        token_type=expected_type,
        issued_at=datetime.fromtimestamp(issued_at, tz=UTC),
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
        token_id=token_id,
    )


def get_remaining_token_ttl_seconds(token: str) -> int:
    """Return remaining access-token lifetime for Redis blacklist TTL."""

    decoded_token = decode_token(token, TokenType.ACCESS)
    remaining_seconds = int(
        (decoded_token["expires_at"] - datetime.now(UTC)).total_seconds()
    )
    return max(remaining_seconds, 0)


def hash_token_for_storage(token: str) -> str:
    """Create a non-reversible token hash for database storage."""

    settings = get_settings()
    return hmac.new(
        settings.jwt_secret_key.get_secret_value().encode("utf-8"),
        token.encode("utf-8"),
        sha256,
    ).hexdigest()


def _create_token(
    *,
    user_id: UUID,
    token_type: TokenType,
    expires_delta: timedelta,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    payload: dict[str, str | int] = {
        "sub": str(user_id),
        "type": token_type.value,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    return cast(
        str,
        jwt.encode(
            payload,
            settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        ),
    )
