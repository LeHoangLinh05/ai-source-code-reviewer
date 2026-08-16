"""Tests for centralized application configuration."""

from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import POSTGRES_ASYNC_DRIVER, Settings, normalize_postgres_url

JWT_SECRET_KEY = "test-secret-key-with-at-least-32-characters"


def test_normalize_postgres_url_preserves_neon_security_options() -> None:
    postgres_url = (
        "postgresql://repoguard:secret@db.example.com/repoguard"
        "?sslmode=require&channel_binding=require"
    )

    normalized_url = make_url(normalize_postgres_url(postgres_url))

    assert normalized_url.drivername == POSTGRES_ASYNC_DRIVER
    assert normalized_url.query == {
        "sslmode": "require",
        "channel_binding": "require",
    }


def test_normalize_postgres_url_replaces_asyncpg_driver() -> None:
    postgres_url = "postgresql+asyncpg://repoguard@localhost/repoguard"

    normalized_url = make_url(normalize_postgres_url(postgres_url))

    assert normalized_url.drivername == POSTGRES_ASYNC_DRIVER
    assert normalized_url.host == "localhost"
    assert normalized_url.database == "repoguard"


def test_normalize_postgres_url_preserves_encoded_credentials() -> None:
    postgres_url = "postgresql://repo%40guard:p%40ss%3Aword@db.example.com/repoguard"

    normalized_url = make_url(normalize_postgres_url(postgres_url))

    assert normalized_url.username == "repo@guard"
    assert normalized_url.password == "p@ss:word"


def test_settings_normalizes_postgres_url_from_deployment_alias() -> None:
    settings = Settings.model_validate(
        {
            "POSTGRES_URL": (
                "postgresql://repoguard:secret@db.example.com/repoguard"
                "?sslmode=require&channel_binding=require"
            ),
            "JWT_SECRET_KEY": JWT_SECRET_KEY,
        }
    )

    normalized_url = make_url(settings.postgres_url)

    assert normalized_url.drivername == POSTGRES_ASYNC_DRIVER
    assert normalized_url.query["sslmode"] == "require"
    assert normalized_url.query["channel_binding"] == "require"


def test_settings_allows_valid_bot_configuration() -> None:
    settings = Settings(
        jwt_secret_key=SecretStr(JWT_SECRET_KEY),
        github_bot_username="repoguard-bot",
        github_bot_token=SecretStr("ghp_1234567890"),
    )

    assert settings.github_bot_username == "repoguard-bot"
    assert settings.github_bot_token is not None
    assert settings.github_bot_token.get_secret_value() == "ghp_1234567890"


def test_settings_rejects_partial_bot_configuration() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="GITHUB_BOT_USERNAME"):
        Settings(
            jwt_secret_key=SecretStr(JWT_SECRET_KEY),
            github_bot_username="repoguard-bot",
            github_bot_token=None,
        )

    with pytest.raises(ValidationError, match="GITHUB_BOT_USERNAME"):
        Settings(
            jwt_secret_key=SecretStr(JWT_SECRET_KEY),
            github_bot_username=None,
            github_bot_token=SecretStr("ghp_1234567890"),
        )
