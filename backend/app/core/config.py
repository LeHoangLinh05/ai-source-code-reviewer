"""Application settings loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import SecretStr
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
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    llm_provider: str = "gemini"
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
