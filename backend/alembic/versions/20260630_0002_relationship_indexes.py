"""Add relationship support indexes.

Revision ID: 20260630_0002
Revises: 20260630_0001
Create Date: 2026-06-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260630_0002"
down_revision: str | None = "20260630_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add indexes for FK traversal and common auth/review filters."""

    op.create_index(
        "idx_review_jobs_repository",
        "review_jobs",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "idx_refresh_tokens_user_revoked",
        "refresh_tokens",
        ["user_id", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "idx_status_history_job_changed",
        "job_status_history",
        ["job_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove indexes added for ORM relationship traversal."""

    op.drop_index(
        "idx_status_history_job_changed",
        table_name="job_status_history",
    )
    op.drop_index(
        "idx_refresh_tokens_user_revoked",
        table_name="refresh_tokens",
    )
    op.drop_index("idx_review_jobs_repository", table_name="review_jobs")
