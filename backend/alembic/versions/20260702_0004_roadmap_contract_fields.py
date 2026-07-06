"""Add roadmap compliance report contract fields.

Revision ID: 20260702_0004
Revises: 20260701_0003
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260702_0004"
down_revision: str | None = "20260701_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prepare PostgreSQL contracts used by roadmap compliance results."""

    op.execute("ALTER TYPE issue_category ADD VALUE IF NOT EXISTS 'requirement'")
    op.execute("ALTER TYPE issue_source ADD VALUE IF NOT EXISTS 'roadmap_rule'")
    op.alter_column("review_issues", "file_path", nullable=True)
    op.add_column(
        "review_reports",
        sa.Column("compliance_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "review_reports",
        sa.Column("bonus_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Remove roadmap compliance columns and restore file-path strictness."""

    op.drop_column("review_reports", "bonus_score")
    op.drop_column("review_reports", "compliance_score")
    op.alter_column("review_issues", "file_path", nullable=False)
