"""Add per-issue planning and verification contracts to fix jobs."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260808_0012"
down_revision = "20260731_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store planner and verifier output for current and future fix jobs."""

    empty_array = sa.text("'[]'::jsonb")
    op.add_column(
        "fix_jobs",
        sa.Column(
            "issue_plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=empty_array,
            nullable=False,
        ),
    )
    op.add_column(
        "fix_jobs",
        sa.Column(
            "issue_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=empty_array,
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove per-issue fix contracts."""

    op.drop_column("fix_jobs", "issue_results")
    op.drop_column("fix_jobs", "issue_plan")
