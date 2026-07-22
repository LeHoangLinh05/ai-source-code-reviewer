"""Tests for authentication input validation."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.user import ChangePasswordRequest

VALID_EMAIL = "User.Example+test@example.com"
NORMALIZED_EMAIL = "user.example+test@example.com"
VALID_PASSWORD = "Valid@123"
LEGACY_TEST_LOGIN_EMAIL = "user1@gmail.com"
LEGACY_TEST_LOGIN_PASSWORD = "12345678"


@pytest.mark.parametrize(
    "email",
    [
        "",
        "abc.com",
        "abc@",
        "abc@@gmail.com",
        " test@example.com",
        "test@example.com ",
        "tên@gmail.com",
        "' OR '1'='1",
        "<script>alert('XSS')</script>",
        "user@-example.com",
        "user@example-.com",
        "a" * 246 + "@example.com",
    ],
)
def test_register_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email=email, password=VALID_PASSWORD)


@pytest.mark.parametrize(
    "email",
    [
        "abc.com",
        "abc@",
        "abc@@gmail.com",
        " not-an-email@example.com",
        "tên@gmail.com",
    ],
)
def test_login_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email=email, password=VALID_PASSWORD)


@pytest.mark.parametrize(
    "password",
    [
        "",
        "123",
        "12345678",
        "abcdefgh",
        "ABCDEFGH",
        "Password",
        "Password1",
        "Valid @123",
        " Valid@123",
        "Valid@123 ",
        "Mật khẩu😀123",
        "A" * 129,
    ],
)
def test_register_rejects_weak_or_unsafe_password(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email=VALID_EMAIL, password=password)


@pytest.mark.parametrize("password", ["12345678", "Valid @123", "Mật khẩu😀123"])
def test_login_rejects_weak_or_unsafe_password(password: str) -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email=VALID_EMAIL, password=password)


def test_login_allows_legacy_test_account_password() -> None:
    payload = LoginRequest(
        email=LEGACY_TEST_LOGIN_EMAIL,
        password=LEGACY_TEST_LOGIN_PASSWORD,
    )

    assert payload.email == LEGACY_TEST_LOGIN_EMAIL
    assert payload.password == LEGACY_TEST_LOGIN_PASSWORD


def test_auth_schemas_normalize_valid_email() -> None:
    register_payload = RegisterRequest(email=VALID_EMAIL, password=VALID_PASSWORD)
    login_payload = LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)

    assert register_payload.email == NORMALIZED_EMAIL
    assert login_payload.email == NORMALIZED_EMAIL


def test_change_password_reuses_strong_password_policy() -> None:
    payload = ChangePasswordRequest(
        current_password="current-password",
        new_password=VALID_PASSWORD,
    )

    assert payload.new_password == VALID_PASSWORD


def test_change_password_rejects_weak_new_password() -> None:
    with pytest.raises(ValidationError):
        ChangePasswordRequest(
            current_password="current-password",
            new_password="new-password",
        )
