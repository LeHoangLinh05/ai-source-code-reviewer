"""Remove unsupported repository platform enum values.

Revision ID: 20260815_0015
Revises: 20260810_0014
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_0015"
down_revision: str | None = "20260810_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Restrict stored repository platforms to the supported domain."""

    op.execute("ALTER TYPE repository_platform RENAME TO repository_platform_legacy")
    op.execute("CREATE TYPE repository_platform AS ENUM ('github', 'other')")
    op.execute(
        """
        ALTER TABLE repositories
        ALTER COLUMN platform TYPE repository_platform
        USING (
            CASE
                WHEN platform IS NULL THEN NULL
                WHEN platform::text = 'github' THEN 'github'
                ELSE 'other'
            END
        )::repository_platform
        """
    )
    op.execute(
        """
        ALTER TABLE fix_jobs
        ALTER COLUMN provider TYPE repository_platform
        USING (
            CASE
                WHEN provider IS NULL THEN NULL
                WHEN provider::text = 'github' THEN 'github'
                ELSE 'other'
            END
        )::repository_platform
        """
    )
    op.execute(
        """
        ALTER TABLE provider_installations
        ALTER COLUMN provider TYPE repository_platform
        USING (
            CASE
                WHEN provider::text = 'github' THEN 'github'
                ELSE 'other'
            END
        )::repository_platform
        """
    )
    op.execute("DROP TYPE repository_platform_legacy")


def downgrade() -> None:
    """Keep the restricted platform domain because removed values are obsolete."""
