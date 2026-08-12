"""Add the auditable publish override reason to fix jobs."""

import sqlalchemy as sa

from alembic import op

revision = "20260809_0013"
down_revision = "20260808_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store the reason supplied for an unverified manual publish."""

    op.add_column(
        "fix_jobs",
        sa.Column("publish_override_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the manual publish override reason."""

    op.drop_column("fix_jobs", "publish_override_reason")
