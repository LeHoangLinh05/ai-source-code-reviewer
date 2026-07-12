"""Add review job status for repo summary generation.

Revision ID: 20260712_0007
Revises: 20260711_0006
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260712_0007"
down_revision: str | None = "20260711_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow the worker to persist the non-blocking Repo Summary step."""

    op.execute(
        "ALTER TYPE review_job_status ADD VALUE IF NOT EXISTS 'GENERATING_SUMMARY'"
    )


def downgrade() -> None:
    """Keep enum value on downgrade; PostgreSQL cannot drop enum labels safely."""
