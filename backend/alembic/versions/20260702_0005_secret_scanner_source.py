"""Add secret scanner issue source.

Revision ID: 20260702_0005
Revises: 20260702_0004
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260702_0005"
down_revision: str | None = "20260702_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow regex secret scanner findings to be stored as review issues."""

    op.execute("ALTER TYPE issue_source ADD VALUE IF NOT EXISTS 'secret_scanner'")


def downgrade() -> None:
    """PostgreSQL enum values are intentionally not removed on downgrade."""
