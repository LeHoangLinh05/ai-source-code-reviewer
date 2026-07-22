"""Tests for authenticated user settings workflows."""

import pytest

from app.core.exceptions import AuthenticationError, BadRequestError
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.schemas.user import ChangePasswordRequest, UserProfileUpdateRequest
from app.services.user_service import UserService


@pytest.mark.asyncio
async def test_update_profile_stores_full_name() -> None:
    user = build_user()
    repository = FakeUserRepository()
    service = UserService(repository)  # type: ignore[arg-type]

    updated_user = await service.update_profile(
        user,
        UserProfileUpdateRequest(full_name="Ada Lovelace"),
    )

    assert updated_user.full_name == "Ada Lovelace"
    assert repository.committed is True
    assert repository.rolled_back is False


@pytest.mark.asyncio
async def test_change_password_updates_hash() -> None:
    user = build_user(password="old-password")
    repository = FakeUserRepository()
    service = UserService(repository)  # type: ignore[arg-type]

    await service.change_password(
        user,
        ChangePasswordRequest(
            current_password="old-password",
            new_password="new-password",
        ),
    )

    assert verify_password("new-password", user.hashed_password)
    assert repository.committed is True
    assert repository.rolled_back is False


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password() -> None:
    user = build_user(password="old-password")
    service = UserService(FakeUserRepository())  # type: ignore[arg-type]

    with pytest.raises(AuthenticationError, match="Current password is incorrect"):
        await service.change_password(
            user,
            ChangePasswordRequest(
                current_password="bad-password",
                new_password="new-password",
            ),
        )


@pytest.mark.asyncio
async def test_change_password_rejects_same_password() -> None:
    user = build_user(password="same-password")
    service = UserService(FakeUserRepository())  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="New password must be different"):
        await service.change_password(
            user,
            ChangePasswordRequest(
                current_password="same-password",
                new_password="same-password",
            ),
        )


class FakeUserRepository:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def update_profile(
        self,
        user: User,
        *,
        full_name: str | None,
    ) -> User:
        user.full_name = full_name
        return user

    async def update_password(
        self,
        user: User,
        *,
        hashed_password: str,
    ) -> None:
        user.hashed_password = hashed_password

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def build_user(password: str = "password") -> User:
    return User(
        email="user@example.com",
        hashed_password=hash_password(password),
    )
