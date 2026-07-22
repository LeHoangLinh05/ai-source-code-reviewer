"""Add user-wide refresh token revoke cutoff.

Revision ID: 20260701_0003
Revises: 20260630_0002
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260701_0003"
down_revision: str | None = "20260630_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store a cutoff timestamp for revoking all refresh tokens of a user."""

    op.add_column(
        "users",
        sa.Column(
            "refresh_tokens_revoked_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """Remove the user-wide refresh-token revoke cutoff."""

    op.drop_column("users", "refresh_tokens_revoked_at")
