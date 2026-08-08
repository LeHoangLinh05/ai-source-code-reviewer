"""Make fork publishing the only fix publish strategy."""

from alembic import op

revision = "20260731_0011"
down_revision = "20260731_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Set the database default used for new fix jobs."""

    op.alter_column(
        "fix_jobs",
        "publish_strategy",
        server_default="fork",
    )


def downgrade() -> None:
    """Restore the legacy publish default."""

    op.alter_column(
        "fix_jobs",
        "publish_strategy",
        server_default="auto",
    )
