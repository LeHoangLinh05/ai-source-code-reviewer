"""Add a qualitative AI overview to review reports."""

import sqlalchemy as sa

from alembic import op

revision = "20260810_0014"
down_revision = "20260809_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store AI prose separately from deterministic report statistics."""

    op.add_column(
        "review_reports",
        sa.Column("analysis_overview", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the qualitative AI report overview."""

    op.drop_column("review_reports", "analysis_overview")
