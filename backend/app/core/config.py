"""Application settings loaded from environment variables and .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the RepoGuard AI backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "RepoGuard AI"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "repoguard_ai"
    postgres_user: str = "repoguard"
    postgres_password: SecretStr = SecretStr("")
    postgres_url: str = "postgresql+asyncpg://repoguard@localhost:5432/repoguard_ai"

    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_db: str = "repoguard_ai"
    mongodb_user: str | None = None
    mongodb_password: SecretStr | None = None
    mongodb_url: str = "mongodb://localhost:27017/repoguard_ai"

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
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
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


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
