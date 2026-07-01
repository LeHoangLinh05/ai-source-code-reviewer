"""Authentication business workflows and token orchestration."""

from datetime import UTC, datetime
import logging
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InactiveUserError,
)
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token_for_storage,
    hash_password,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenPairResponse,
    UserResponse,
)
from app.services.token_blacklist import TokenBlacklistService

logger = logging.getLogger(__name__)


class AuthService:
    """Business workflow for registration, login, refresh, and logout."""

    def __init__(
        self,
        user_repository: UserRepository,
        refresh_token_repository: RefreshTokenRepository,
        token_blacklist_service: TokenBlacklistService,
    ) -> None:
        self.user_repository = user_repository
        self.refresh_token_repository = refresh_token_repository
        self.token_blacklist_service = token_blacklist_service

    async def register(self, payload: RegisterRequest) -> TokenPairResponse:
        """Create a user account and issue the first token pair."""

        existing_user = await self.user_repository.get_by_email(payload.email)
        if existing_user is not None:
            raise ConflictError("Email is already registered")

        user = await self.user_repository.create(
            email=payload.email,
            hashed_password=hash_password(payload.password),
        )
        return await self._issue_token_pair(user)

    async def login(self, payload: LoginRequest) -> TokenPairResponse:
        """Verify credentials and issue a fresh token pair."""

        user = await self.user_repository.get_by_email(payload.email)
        if user is None:
            raise AuthenticationError("Invalid email or password")

        if not verify_password(payload.password, user.hashed_password):
            raise AuthenticationError("Invalid email or password")

        if not user.is_active:
            raise InactiveUserError()

        return await self._issue_token_pair(user)

    async def refresh(self, refresh_token: str) -> TokenPairResponse:
        """Validate a refresh token and rotate to a new token pair."""

        decoded_token = decode_token(refresh_token, TokenType.REFRESH)
        stored_refresh_token = await self._get_valid_refresh_token(
            refresh_token,
            user_id=decoded_token["subject"],
        )
        user = await self.user_repository.get_by_id(stored_refresh_token.user_id)
        if user is None:
            raise AuthenticationError("Refresh token user no longer exists")

        if not user.is_active:
            raise InactiveUserError()

        if self._is_refresh_token_revoked_for_user(decoded_token["issued_at"], user):
            raise AuthenticationError("Refresh token has been revoked")

        return await self._rotate_refresh_token(user, stored_refresh_token)

    async def logout(self, access_token: str, refresh_token: str | None = None) -> None:
        """Revoke the current access token and matching refresh token."""

        decoded_access_token = decode_token(access_token, TokenType.ACCESS)
        ttl_seconds = int(
            (decoded_access_token["expires_at"] - datetime.now(UTC)).total_seconds()
        )
        await self.token_blacklist_service.add_to_blacklist(
            decoded_access_token["token_id"],
            max(ttl_seconds, 0),
        )
        settings = get_settings()
        await self.token_blacklist_service.invalidate_user_tokens_issued_before(
            decoded_access_token["subject"],
            datetime.now(UTC),
            settings.jwt_access_token_expire_minutes * 60,
        )
        if refresh_token is None:
            return

        await self._revoke_refresh_token(refresh_token)

    async def logout_all(self, access_token: str, user: User) -> None:
        """Revoke all refresh tokens and access tokens issued for a user."""

        decoded_access_token = decode_token(access_token, TokenType.ACCESS)
        if decoded_access_token["subject"] != user.id:
            raise AuthenticationError("Access token subject mismatch")

        now = datetime.now(UTC)

        try:
            await self.user_repository.mark_refresh_tokens_revoked(
                user,
                revoked_at=now,
            )
            await self.refresh_token_repository.revoke_active_for_user(
                user_id=user.id,
                revoked_at=now,
            )
            await self.user_repository.commit()
        except Exception:
            await self.user_repository.rollback()
            raise

        ttl_seconds = int(
            (decoded_access_token["expires_at"] - datetime.now(UTC)).total_seconds()
        )
        await self.token_blacklist_service.add_to_blacklist(
            decoded_access_token["token_id"],
            max(ttl_seconds, 0),
        )
        settings = get_settings()
        await self.token_blacklist_service.invalidate_user_tokens_issued_before(
            user.id,
            now,
            settings.jwt_access_token_expire_minutes * 60,
        )

    async def get_active_user(self, user_id: UUID) -> User:
        """Load an active user for token-authenticated requests."""

        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("Token user no longer exists")

        if not user.is_active:
            raise InactiveUserError()

        return user

    async def _issue_token_pair(self, user: User) -> TokenPairResponse:
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        decoded_refresh_token = decode_token(refresh_token, TokenType.REFRESH)

        try:
            await self.refresh_token_repository.create(
                user_id=user.id,
                token_hash=hash_token_for_storage(refresh_token),
                expires_at=decoded_refresh_token["expires_at"],
            )
            await self.refresh_token_repository.commit()
        except Exception:
            await self.refresh_token_repository.rollback()
            raise

        return TokenPairResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    async def _rotate_refresh_token(
        self,
        user: User,
        stored_refresh_token: RefreshToken,
    ) -> TokenPairResponse:
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        decoded_refresh_token = decode_token(refresh_token, TokenType.REFRESH)
        now = datetime.now(UTC)

        try:
            replacement_token = await self.refresh_token_repository.create(
                user_id=user.id,
                token_hash=hash_token_for_storage(refresh_token),
                expires_at=decoded_refresh_token["expires_at"],
            )
            await self.refresh_token_repository.revoke(
                stored_refresh_token,
                revoked_at=now,
                replaced_by_token_id=replacement_token.id,
            )
            await self.refresh_token_repository.commit()
        except Exception:
            await self.refresh_token_repository.rollback()
            raise

        return TokenPairResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserResponse.model_validate(user),
        )

    async def _revoke_refresh_token(self, refresh_token: str) -> None:
        try:
            decoded_token = decode_token(refresh_token, TokenType.REFRESH)
        except AuthenticationError:
            return

        stored_refresh_token = await self.refresh_token_repository.get_by_hash(
            hash_token_for_storage(refresh_token)
        )
        if stored_refresh_token is None:
            return

        if stored_refresh_token.user_id != decoded_token["subject"]:
            return

        if stored_refresh_token.revoked_at is not None:
            return

        try:
            await self.refresh_token_repository.revoke(
                stored_refresh_token,
                revoked_at=datetime.now(UTC),
            )
            await self.refresh_token_repository.commit()
        except Exception:
            await self.refresh_token_repository.rollback()
            raise

    async def _get_valid_refresh_token(
        self,
        refresh_token: str,
        *,
        user_id: UUID,
    ) -> RefreshToken:
        stored_refresh_token = await self.refresh_token_repository.get_by_hash(
            hash_token_for_storage(refresh_token)
        )
        if stored_refresh_token is None:
            raise AuthenticationError("Refresh token has been revoked")

        if stored_refresh_token.user_id != user_id:
            raise AuthenticationError("Refresh token subject mismatch")

        if stored_refresh_token.revoked_at is not None:
            raise AuthenticationError("Refresh token has been revoked")

        if stored_refresh_token.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired")

        return stored_refresh_token

    def _is_refresh_token_revoked_for_user(
        self,
        issued_at: datetime,
        user: User,
    ) -> bool:
        if user.refresh_tokens_revoked_at is None:
            return False

        return issued_at < user.refresh_tokens_revoked_at
