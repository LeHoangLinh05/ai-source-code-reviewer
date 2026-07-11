"""Merge roadmap-derived findings into the normal KB issue flow.

Revision ID: 20260711_0006
Revises: 20260702_0005
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260711_0006"
down_revision: str | None = "20260702_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add KB issue source and remove obsolete roadmap report data."""

    op.execute(
        "DELETE FROM review_issues "
        "WHERE source = 'roadmap_rule' "
        "OR file_path IS NULL OR line_start IS NULL OR line_end IS NULL"
    )
    op.execute("ALTER TYPE issue_source RENAME TO issue_source_legacy")
    op.execute(
        "CREATE TYPE issue_source AS ENUM "
        "('ai_review', 'ruff', 'bandit', 'eslint', 'KB', 'secret_scanner')"
    )
    op.execute(
        "ALTER TABLE review_issues ALTER COLUMN source TYPE issue_source "
        "USING source::text::issue_source"
    )
    op.execute("DROP TYPE issue_source_legacy")
    op.alter_column("review_issues", "file_path", nullable=False)
    op.alter_column("review_issues", "line_start", nullable=False)
    op.alter_column("review_issues", "line_end", nullable=False)
    op.drop_column("review_reports", "bonus_score")
    op.drop_column("review_reports", "compliance_score")


def downgrade() -> None:
    """Restore legacy report columns and nullable issue locations."""

    op.execute("ALTER TYPE issue_source RENAME TO issue_source_current")
    op.execute(
        "CREATE TYPE issue_source AS ENUM "
        "('ai_review', 'ruff', 'bandit', 'eslint', 'roadmap_rule', "
        "'secret_scanner')"
    )
    op.execute(
        "ALTER TABLE review_issues ALTER COLUMN source TYPE issue_source "
        "USING (CASE WHEN source::text = 'KB' THEN 'roadmap_rule' "
        "ELSE source::text END)::issue_source"
    )
    op.execute("DROP TYPE issue_source_current")
    op.add_column(
        "review_reports",
        sa.Column("compliance_score", sa.Float()),
    )
    op.add_column(
        "review_reports",
        sa.Column("bonus_score", sa.Float()),
    )
    op.alter_column("review_issues", "line_end", nullable=True)
    op.alter_column("review_issues", "line_start", nullable=True)
    op.alter_column("review_issues", "file_path", nullable=True)
