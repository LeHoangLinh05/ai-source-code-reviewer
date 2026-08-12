"""Normalize legacy admin accounts to the single user role.

Revision ID: 20260728_0008
Revises: 20260712_0007
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0008"
down_revision: str | None = "20260712_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert every legacy admin account into a regular user account."""

    op.execute("UPDATE users SET role = 'user' WHERE role = 'admin'")


def downgrade() -> None:
    """Keep normalized roles because previous admin membership is unrecoverable."""
