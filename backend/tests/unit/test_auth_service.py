"""Tests for authentication service workflows."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.core.exceptions import AuthenticationError, ConflictError, InactiveUserError
from app.core.security import (
    TokenType,
    decode_token,
    hash_password,
    hash_token_for_storage,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService

JWT_SECRET = "x" * 32
VALID_EMAIL = "user@example.com"
VALID_PASSWORD = "Valid@123"


@pytest.fixture(autouse=True)
def use_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        jwt_secret_key=SecretStr(JWT_SECRET),
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=7,
    )
    monkeypatch.setattr("app.core.security.get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_register_creates_user_without_issuing_tokens() -> None:
    service = build_service()

    user = await service.register(
        RegisterRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )

    assert user.email == VALID_EMAIL
    assert service.user_repository.created_user is not None
    assert service.refresh_token_repository.tokens_by_hash == {}


@pytest.mark.asyncio
async def test_register_rejects_duplicate_email() -> None:
    service = build_service(users=[build_user(email=VALID_EMAIL)])

    with pytest.raises(ConflictError, match="Email already exists"):
        await service.register(
            RegisterRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
        )


@pytest.mark.asyncio
async def test_login_returns_token_pair_and_stores_refresh_hash() -> None:
    user = build_user(email=VALID_EMAIL, password=VALID_PASSWORD)
    service = build_service(users=[user])

    token_pair = await service.login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )
    decoded_access_token = decode_token(token_pair.access_token, TokenType.ACCESS)
    decoded_refresh_token = decode_token(token_pair.refresh_token, TokenType.REFRESH)

    assert token_pair.token_type == "Bearer"
    assert token_pair.user.email == VALID_EMAIL
    assert decoded_access_token["subject"] == user.id
    assert decoded_refresh_token["subject"] == user.id
    assert hash_token_for_storage(token_pair.refresh_token) in (
        service.refresh_token_repository.tokens_by_hash
    )


@pytest.mark.asyncio
async def test_login_rejects_missing_user_and_wrong_password_with_same_error() -> None:
    service = build_service(users=[build_user(email=VALID_EMAIL)])
    missing_user_payload = LoginRequest(
        email="missing@example.com",
        password=VALID_PASSWORD,
    )
    wrong_password_payload = LoginRequest(
        email=VALID_EMAIL,
        password="Wrong@123",
    )

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        await service.login(missing_user_payload)

    with pytest.raises(AuthenticationError, match="Invalid email or password"):
        await service.login(wrong_password_payload)


@pytest.mark.asyncio
async def test_login_rejects_inactive_user() -> None:
    user = build_user(email=VALID_EMAIL, password=VALID_PASSWORD, is_active=False)
    service = build_service(users=[user])

    with pytest.raises(InactiveUserError):
        await service.login(LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD))


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_rejects_reuse() -> None:
    user = build_user(email=VALID_EMAIL, password=VALID_PASSWORD)
    service = build_service(users=[user])
    original_pair = await service.login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )

    rotated_pair = await service.refresh(original_pair.refresh_token)
    original_record = service.refresh_token_repository.tokens_by_hash[
        hash_token_for_storage(original_pair.refresh_token)
    ]

    assert rotated_pair.refresh_token != original_pair.refresh_token
    assert original_record.revoked_at is not None
    assert original_record.replaced_by_token_id is not None

    with pytest.raises(AuthenticationError, match="Refresh token has been revoked"):
        await service.refresh(original_pair.refresh_token)


@pytest.mark.asyncio
async def test_logout_blacklists_access_token_and_revokes_refresh_token() -> None:
    user = build_user(email=VALID_EMAIL, password=VALID_PASSWORD)
    service = build_service(users=[user])
    token_pair = await service.login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )
    decoded_access_token = decode_token(token_pair.access_token, TokenType.ACCESS)

    await service.logout(token_pair.access_token, token_pair.refresh_token)

    refresh_record = service.refresh_token_repository.tokens_by_hash[
        hash_token_for_storage(token_pair.refresh_token)
    ]
    assert decoded_access_token["token_id"] in service.token_blacklist_service.tokens
    assert user.id in service.token_blacklist_service.invalid_after_by_user
    assert refresh_record.revoked_at is not None


@pytest.mark.asyncio
async def test_logout_all_revokes_every_refresh_token_for_user() -> None:
    user = build_user(email=VALID_EMAIL, password=VALID_PASSWORD)
    service = build_service(users=[user])
    first_pair = await service.login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )
    second_pair = await service.login(
        LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    )

    await service.logout_all(first_pair.access_token, user)

    first_record = service.refresh_token_repository.tokens_by_hash[
        hash_token_for_storage(first_pair.refresh_token)
    ]
    second_record = service.refresh_token_repository.tokens_by_hash[
        hash_token_for_storage(second_pair.refresh_token)
    ]
    assert first_record.revoked_at is not None
    assert second_record.revoked_at is not None


def build_service(users: list[User] | None = None):
    user_repository = FakeUserRepository(users or [])
    refresh_token_repository = FakeRefreshTokenRepository()
    token_blacklist_service = FakeTokenBlacklistService()
    settings = SimpleNamespace(jwt_access_token_expire_minutes=15)
    service = AuthService(
        user_repository=user_repository,  # type: ignore[arg-type]
        refresh_token_repository=refresh_token_repository,  # type: ignore[arg-type]
        token_blacklist_service=token_blacklist_service,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    service.user_repository = user_repository  # type: ignore[assignment]
    service.refresh_token_repository = refresh_token_repository  # type: ignore[assignment]
    service.token_blacklist_service = token_blacklist_service  # type: ignore[assignment]
    return service


def build_user(
    *,
    email: str = VALID_EMAIL,
    password: str = VALID_PASSWORD,
    is_active: bool = True,
    role: UserRole = UserRole.USER,
) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        email=email,
        hashed_password=hash_password(password),
        full_name=None,
        role=role,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


class FakeUserRepository:
    def __init__(self, users: list[User]) -> None:
        self.users_by_id = {user.id: user for user in users}
        self.users_by_email = {user.email: user for user in users}
        self.created_user: User | None = None
        self.committed = False
        self.rolled_back = False

    async def get_by_email(self, email: str) -> User | None:
        return self.users_by_email.get(email)

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users_by_id.get(user_id)

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
    ) -> User:
        now = datetime.now(UTC)
        user = User(
            id=uuid4(),
            email=email,
            hashed_password=hashed_password,
            full_name=None,
            role=UserRole.USER,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.users_by_id[user.id] = user
        self.users_by_email[user.email] = user
        self.created_user = user
        return user

    async def mark_refresh_tokens_revoked(
        self,
        user: User,
        *,
        revoked_at: datetime,
    ) -> None:
        user.refresh_tokens_revoked_at = revoked_at

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeRefreshTokenRepository:
    def __init__(self) -> None:
        self.tokens_by_hash: dict[str, RefreshToken] = {}
        self.committed = False
        self.rolled_back = False

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self.tokens_by_hash.get(token_hash)

    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        token = RefreshToken(
            id=uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked_at=None,
            replaced_by_token_id=None,
        )
        self.tokens_by_hash[token_hash] = token
        return token

    async def revoke(
        self,
        refresh_token: RefreshToken,
        *,
        revoked_at: datetime,
        replaced_by_token_id: UUID | None = None,
    ) -> None:
        refresh_token.revoked_at = revoked_at
        refresh_token.replaced_by_token_id = replaced_by_token_id

    async def revoke_active_for_user(
        self,
        *,
        user_id: UUID,
        revoked_at: datetime,
    ) -> None:
        for token in self.tokens_by_hash.values():
            if token.user_id == user_id and token.revoked_at is None:
                token.revoked_at = revoked_at

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeTokenBlacklistService:
    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}
        self.invalid_after_by_user: dict[UUID, datetime] = {}

    async def add_to_blacklist(self, jti: str, ttl_seconds: int) -> None:
        self.tokens[jti] = ttl_seconds

    async def invalidate_user_tokens_issued_before(
        self,
        user_id: UUID,
        invalid_after: datetime,
        _ttl_seconds: int,
    ) -> None:
        self.invalid_after_by_user[user_id] = invalid_after

    async def is_blacklisted(self, jti: str) -> bool:
        return jti in self.tokens

    async def is_user_token_invalid(
        self,
        user_id: UUID,
        issued_at: datetime,
    ) -> bool:
        invalid_after = self.invalid_after_by_user.get(user_id)
        return invalid_after is not None and issued_at <= invalid_after
