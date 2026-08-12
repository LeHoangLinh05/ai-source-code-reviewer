"""Add fix publish, provider installation, and audit state.

Revision ID: 20260731_0010
Revises: 20260730_0009
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0010"
down_revision: str | None = "20260730_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add publish provider metadata and append-only audit entries."""

    bind = op.get_bind()
    fix_publish_status = postgresql.ENUM(
        "NOT_REQUESTED",
        "PUBLISHING",
        "PUBLISHED",
        "FAILED",
        "NEEDS_FORK",
        "STALE_BASE",
        name="fix_publish_status",
        create_type=False,
    )
    fix_audit_action = postgresql.ENUM(
        "FIX_GENERATED",
        "DIFF_VIEWED",
        "PUBLISH_APPROVED",
        "BRANCH_PUSHED",
        "PR_CREATED",
        "PUBLISH_FAILED",
        "PUBLISH_RETRIED",
        "PUBLISH_CANCELED",
        "NEEDS_FORK",
        "STALE_BASE",
        name="fix_audit_action",
        create_type=False,
    )
    repository_platform = postgresql.ENUM(
        "github",
        "gitlab",
        "other",
        name="repository_platform",
        create_type=False,
    )
    fix_publish_status.create(bind, checkfirst=True)
    fix_audit_action.create(bind, checkfirst=True)

    op.add_column(
        "fix_jobs",
        sa.Column(
            "publish_status",
            fix_publish_status,
            server_default="NOT_REQUESTED",
            nullable=False,
        ),
    )
    op.add_column("fix_jobs", sa.Column("publish_error", sa.Text(), nullable=True))
    op.add_column(
        "fix_jobs",
        sa.Column(
            "publish_strategy",
            sa.String(length=20),
            server_default="auto",
            nullable=False,
        ),
    )
    op.add_column(
        "fix_jobs",
        sa.Column(
            "publish_allow_failed_validation",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("published_branch", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("published_commit_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("provider", repository_platform, nullable=True),
    )
    op.add_column(
        "fix_jobs", sa.Column("publish_sandbox_path", sa.Text(), nullable=True)
    )
    op.add_column(
        "fix_jobs",
        sa.Column("publish_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("publish_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("fork_repository_full_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column("fork_branch", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "fix_jobs",
        sa.Column(
            "upstream_repository_full_name", sa.String(length=255), nullable=True
        ),
    )
    op.create_index(
        "idx_fix_jobs_publish_status",
        "fix_jobs",
        ["publish_status"],
        unique=False,
    )

    op.create_table(
        "provider_installations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", repository_platform, nullable=False),
        sa.Column("installation_id", sa.String(length=80), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=50), nullable=True),
        sa.Column("repository_selection", sa.String(length=50), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "installation_id",
            name="uq_provider_installations_user_provider_installation",
        ),
    )
    op.create_index(
        "idx_provider_installations_provider",
        "provider_installations",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "idx_provider_installations_user",
        "provider_installations",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "fix_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fix_job_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", fix_audit_action, nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fix_job_id"], ["fix_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_fix_audit_logs_fix_job_created",
        "fix_audit_logs",
        ["fix_job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_fix_audit_logs_user",
        "fix_audit_logs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove publish provider metadata and audit entries."""

    op.drop_index("idx_fix_audit_logs_user", table_name="fix_audit_logs")
    op.drop_index("idx_fix_audit_logs_fix_job_created", table_name="fix_audit_logs")
    op.drop_table("fix_audit_logs")

    op.drop_index(
        "idx_provider_installations_user", table_name="provider_installations"
    )
    op.drop_index(
        "idx_provider_installations_provider",
        table_name="provider_installations",
    )
    op.drop_table("provider_installations")

    op.drop_index("idx_fix_jobs_publish_status", table_name="fix_jobs")
    op.drop_column("fix_jobs", "upstream_repository_full_name")
    op.drop_column("fix_jobs", "fork_branch")
    op.drop_column("fix_jobs", "fork_repository_full_name")
    op.drop_column("fix_jobs", "publish_completed_at")
    op.drop_column("fix_jobs", "publish_started_at")
    op.drop_column("fix_jobs", "publish_sandbox_path")
    op.drop_column("fix_jobs", "provider")
    op.drop_column("fix_jobs", "published_commit_sha")
    op.drop_column("fix_jobs", "published_branch")
    op.drop_column("fix_jobs", "publish_allow_failed_validation")
    op.drop_column("fix_jobs", "publish_strategy")
    op.drop_column("fix_jobs", "publish_error")
    op.drop_column("fix_jobs", "publish_status")

    bind = op.get_bind()
    sa.Enum(name="fix_audit_action").drop(bind, checkfirst=True)
    sa.Enum(name="fix_publish_status").drop(bind, checkfirst=True)
