"""Application settings loaded from environment variables and .env."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration for the RepoGuard AI backend."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "RepoGuard AI"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api"
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        validation_alias=AliasChoices("CORS_ALLOWED_ORIGINS", "CORS_ORIGINS"),
    )
    cors_allowed_origin_regex: str | None = (
        r"^https?://(localhost|127\.0\.0\.1|[0-9]{1,3}(\.[0-9]{1,3}){3}):300[0-9]$"
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "repoguard_ai"
    postgres_user: str = "repoguard"
    postgres_password: SecretStr = SecretStr("")
    postgres_url: str = Field(
        default="postgresql+asyncpg://repoguard@localhost:5432/repoguard_ai",
        validation_alias=AliasChoices("POSTGRES_URL", "DATABASE_URL"),
    )

    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_db: str = "repoguard_ai"
    mongodb_user: str | None = None
    mongodb_password: SecretStr | None = None
    mongodb_url: str = Field(
        default="mongodb://localhost:27017/repoguard_ai",
        validation_alias=AliasChoices("MONGODB_URL", "MONGO_URL"),
    )

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr | None = None
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 10
    redis_socket_connect_timeout_seconds: float = 5.0
    redis_socket_timeout_seconds: float = 5.0
    redis_health_check_interval_seconds: int = 30
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
            "ACCESS_TOKEN_EXPIRE",
        ),
    )
    jwt_refresh_token_expire_days: int = Field(
        default=7,
        validation_alias=AliasChoices(
            "JWT_REFRESH_TOKEN_EXPIRE_DAYS",
            "REFRESH_TOKEN_EXPIRE",
        ),
    )
    access_cookie_name: str = "accessToken"
    refresh_cookie_name: str = "refreshToken"
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    llm_provider: str = "gemini"
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: object) -> object:
        """Accept common deployment labels when DEBUG leaks from the host."""

        if not isinstance(value, str):
            return value

        normalized_value = value.strip().lower()
        if normalized_value in {"debug", "development", "dev"}:
            return True

        if normalized_value in {"release", "production", "prod"}:
            return False

        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> object:
        """Accept JSON arrays or comma-separated origins from .env files."""

        if not isinstance(value, str):
            return value

        normalized_value = value.strip()
        if normalized_value.startswith("["):
            return value

        return [
            origin.strip() for origin in normalized_value.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
