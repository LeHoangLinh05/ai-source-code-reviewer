"""Application settings loaded from environment variables and .env."""

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_SANDBOX_ROOT = PROJECT_ROOT / ".sandbox"
GIT_EXECUTABLE_NAME = "git"
GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
DISABLED_GIT_TERMINAL_PROMPT = "0"
MIN_JWT_SECRET_LENGTH = 32


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
    sandbox_root: str = str(DEFAULT_SANDBOX_ROOT)
    sandbox_ttl_hours: int = 1
    max_repo_size_mb: int = 500
    max_source_file_size_bytes: int = 1_048_576
    analysis_subprocess_timeout_seconds: int = 60

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

    llm_provider: Literal["openai"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = "https://api.cline.bot/api/v1"
    openai_model: str = "cline-pass/qwen3.7-plus"
    openai_max_retries: int = Field(default=6, ge=0, le=10)
    openai_min_request_interval_seconds: float = Field(
        default=1.5,
        ge=0.0,
        le=30.0,
    )
    llm_job_call_budget: int = Field(default=96, ge=1, le=200)
    llm_rate_limit_failure_budget: int = Field(default=1, ge=1, le=5)
    probe_retrieval_max_chunks: int = Field(default=188, ge=1, le=500)
    probe_defect_max_chunks: int = Field(default=120, ge=1, le=300)
    probe_coverage_max_chunks: int = Field(default=24, ge=1, le=200)
    probe_roadmap_max_chunks: int = Field(default=44, ge=1, le=200)
    probe_semantic_query_batch_size: int = Field(default=16, ge=1, le=64)
    probe_semantic_max_query_tokens: int = Field(default=64, ge=1, le=2048)
    probe_judge_max_concurrency: int = Field(default=1, ge=1, le=8)
    probe_judge_max_probes_per_batch: int = Field(default=8, ge=1, le=24)
    probe_judge_max_chunks_per_batch: int = Field(default=24, ge=1, le=80)
    mistral_api_key: SecretStr | None = None
    mistral_base_url: str = "https://api.mistral.ai/v1"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    rag_chroma_path: str = str(PROJECT_ROOT / ".chroma")
    rag_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    code_embedding_provider: Literal[
        "openai",
        "mistral",
        "openrouter",
    ] = "mistral"
    code_embedding_model: str = "codestral-embed-2505"
    code_embedding_base_url: str | None = None
    code_embedding_dimension: int = 1536
    code_embedding_batch_size: int = 16
    code_embedding_max_item_tokens: int = Field(default=1500, ge=1, le=8192)
    code_embedding_max_batch_tokens: int = Field(default=12000, ge=1, le=65536)
    code_embedding_max_concurrency: int = Field(default=2, ge=1, le=8)
    code_embedding_max_pending_batches: int = Field(default=4, ge=1, le=32)
    code_chunk_parse_concurrency: int = Field(default=4, ge=1, le=16)
    mongodb_chunk_batch_size: int = Field(default=500, ge=1, le=5000)
    code_embedding_max_retries: int = Field(default=4, ge=0, le=10)
    code_embedding_retry_base_delay_seconds: float = Field(default=2.0, ge=0.1, le=60.0)
    enable_code_semantic_search: bool = True

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

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        """Require a configured JWT signing secret before the app starts."""

        if len(value.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long",
            )

        return value


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()


def get_git_executable() -> str:
    """Resolve the Git executable before spawning Git subprocesses."""

    git_executable = shutil.which(GIT_EXECUTABLE_NAME)
    if git_executable is None:
        raise RuntimeError("Git executable is unavailable on PATH")

    return git_executable


def build_git_subprocess_env() -> dict[str, str]:
    """Return an environment that disables interactive Git prompts."""

    return {
        **os.environ,
        GIT_TERMINAL_PROMPT_ENV: DISABLED_GIT_TERMINAL_PROMPT,
    }
