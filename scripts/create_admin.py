"""Create an admin user for local testing and later admin pages.

Usage:
    python scripts/create_admin.py
    python scripts/create_admin.py --email admin@example.com

Set ADMIN_EMAIL and ADMIN_PASSWORD in .env to run non-interactively.
"""

from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
import logging
from pathlib import Path
import sys

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.security import hash_password  # noqa: E402
from app.db.postgres import AsyncSessionLocal, close_postgres_engine  # noqa: E402
from app.models.user import UserRole  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.schemas.auth import RegisterRequest  # noqa: E402

logger = logging.getLogger(__name__)


class AdminSeedSettings(BaseSettings):
    """Admin seed credentials loaded from ignored env files."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    admin_email: str | None = None
    admin_password: SecretStr | None = None


async def create_admin(email: str, password: str) -> None:
    """Create one admin user if the email is not already registered."""

    payload = RegisterRequest(email=email, password=password)
    async with AsyncSessionLocal() as session:
        user_repository = UserRepository(session)
        existing_user = await user_repository.get_by_email(payload.email)
        if existing_user is not None:
            raise ValueError(f"User already exists: {payload.email}")

        admin_user = await user_repository.create(
            email=payload.email,
            hashed_password=hash_password(payload.password),
            role=UserRole.ADMIN,
        )
        logger.info("Created admin user %s with id %s", admin_user.email, admin_user.id)


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for admin creation."""

    parser = argparse.ArgumentParser(description="Create a RepoGuard admin user.")
    parser.add_argument("--email", help="Admin email address")
    return parser.parse_args()


def get_admin_password(settings: AdminSeedSettings) -> str | None:
    """Return the configured admin password when it is non-empty."""

    if settings.admin_password is None:
        return None

    password = settings.admin_password.get_secret_value()
    if not password:
        return None

    return password


def read_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Read credentials from environment variables, flags, or prompts."""

    settings = AdminSeedSettings()
    configured_email = settings.admin_email.strip() if settings.admin_email else None
    email = args.email or configured_email or input("Admin email: ").strip()
    password = get_admin_password(settings) or getpass("Admin password: ")
    return email, password


async def main() -> None:
    """Run the admin creation workflow."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    email, password = read_credentials(args)
    try:
        await create_admin(email, password)
    finally:
        await close_postgres_engine()


if __name__ == "__main__":
    asyncio.run(main())
