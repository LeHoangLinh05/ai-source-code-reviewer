"""Add fix job workflow tables.

Revision ID: 20260730_0009
Revises: 20260728_0008
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0009"
down_revision: str | None = "20260728_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create fix job state for generated code patches."""

    fix_job_status = sa.Enum(
        "PENDING",
        "PREPARING",
        "GENERATING_PATCH",
        "VALIDATING",
        "WAITING_APPROVAL",
        "APPROVED",
        "FAILED",
        name="fix_job_status",
    )
    fix_validation_status = sa.Enum(
        "NOT_RUN",
        "PASSED",
        "FAILED",
        name="fix_validation_status",
    )
    op.create_table(
        "fix_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", fix_job_status, server_default="PENDING", nullable=False),
        sa.Column(
            "validation_status",
            fix_validation_status,
            server_default="NOT_RUN",
            nullable=False,
        ),
        sa.Column("issue_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_branch", sa.String(length=100), nullable=False),
        sa.Column("base_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("fix_branch", sa.String(length=150), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sandbox_path", sa.Text(), nullable=True),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column(
            "changed_files",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "validation_output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_job_id"], ["review_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_fix_jobs_review_job",
        "fix_jobs",
        ["review_job_id"],
        unique=False,
    )
    op.create_index("idx_fix_jobs_status", "fix_jobs", ["status"], unique=False)
    op.create_index("idx_fix_jobs_user", "fix_jobs", ["user_id"], unique=False)


def downgrade() -> None:
    """Drop fix job workflow state."""

    op.drop_index("idx_fix_jobs_user", table_name="fix_jobs")
    op.drop_index("idx_fix_jobs_status", table_name="fix_jobs")
    op.drop_index("idx_fix_jobs_review_job", table_name="fix_jobs")
    op.drop_table("fix_jobs")

    bind = op.get_bind()
    sa.Enum(name="fix_validation_status").drop(bind, checkfirst=True)
    sa.Enum(name="fix_job_status").drop(bind, checkfirst=True)
