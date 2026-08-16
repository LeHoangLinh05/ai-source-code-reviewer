"""Drop provider_installations table after GitHub Bot PAT migration.

Revision ID: 20260816_0016
Revises: 20260815_0015
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260816_0016"
down_revision: str | None = "20260815_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop provider_installations table after bot PAT migration."""

    op.drop_table("provider_installations")


def downgrade() -> None:
    """Keep table dropped because provider installations are obsolete."""
