"""Initial PostgreSQL schema.

Revision ID: 20260630_0001
Revises:
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260630_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all PostgreSQL tables used by Phase 1 auth and Phase 3 models."""

    user_role = sa.Enum("user", "admin", name="user_role")
    repository_platform = sa.Enum(
        "github", "gitlab", "other", name="repository_platform"
    )
    review_job_status = sa.Enum(
        "PENDING",
        "CLONING",
        "ANALYZING_STRUCTURE",
        "RUNNING_STATIC_ANALYSIS",
        "CHUNKING_CODE",
        "AI_REVIEWING",
        "GENERATING_REPORT",
        "COMPLETED",
        "FAILED",
        name="review_job_status",
    )
    issue_severity = sa.Enum(
        "critical", "high", "medium", "low", "info", name="issue_severity"
    )
    issue_category = sa.Enum(
        "security",
        "performance",
        "maintainability",
        "style",
        "bug",
        name="issue_category",
    )
    issue_source = sa.Enum("ai_review", "ruff", "bandit", "eslint", name="issue_source")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_pw", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", user_role, server_default="user", nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("platform", repository_platform, nullable=True),
        sa.Column(
            "default_branch",
            sa.String(length=100),
            server_default="main",
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_repositories_user_id"), "repositories", ["user_id"], unique=False
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_token_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_token_id"], ["refresh_tokens.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refresh_tokens_expires_at"),
        "refresh_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"),
        "refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False
    )

    op.create_table(
        "review_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", review_job_status, server_default="PENDING", nullable=False
        ),
        sa.Column("branch", sa.String(length=100), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sandbox_path", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["repositories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_review_jobs_status", "review_jobs", ["status"], unique=False)
    op.create_index("idx_review_jobs_user", "review_jobs", ["user_id"], unique=False)

    op.create_table(
        "job_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["review_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_status_history_job_id"),
        "job_status_history",
        ["job_id"],
        unique=False,
    )

    op.create_table(
        "review_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("severity", issue_severity, nullable=False),
        sa.Column("category", issue_category, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("source", issue_source, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("raw_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["review_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_issues_category", "review_issues", ["category"], unique=False)
    op.create_index("idx_issues_file", "review_issues", ["file_path"], unique=False)
    op.create_index("idx_issues_job", "review_issues", ["job_id"], unique=False)
    op.create_index("idx_issues_severity", "review_issues", ["severity"], unique=False)

    op.create_table(
        "review_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "total_files_analyzed", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("total_issues", sa.Integer(), server_default="0", nullable=False),
        sa.Column("critical_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("high_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("medium_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("low_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("info_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("security_score", sa.Float(), nullable=True),
        sa.Column("maintainability_score", sa.Float(), nullable=True),
        sa.Column("performance_score", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("tech_stack", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "top_risky_files", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("ai_model_used", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["review_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )


def downgrade() -> None:
    """Drop all tables and enum types created by this migration."""

    op.drop_table("review_reports")
    op.drop_index("idx_issues_severity", table_name="review_issues")
    op.drop_index("idx_issues_job", table_name="review_issues")
    op.drop_index("idx_issues_file", table_name="review_issues")
    op.drop_index("idx_issues_category", table_name="review_issues")
    op.drop_table("review_issues")
    op.drop_index(op.f("ix_job_status_history_job_id"), table_name="job_status_history")
    op.drop_table("job_status_history")
    op.drop_index("idx_review_jobs_user", table_name="review_jobs")
    op.drop_index("idx_review_jobs_status", table_name="review_jobs")
    op.drop_table("review_jobs")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_expires_at"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_repositories_user_id"), table_name="repositories")
    op.drop_table("repositories")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    sa.Enum(name="issue_source").drop(bind, checkfirst=True)
    sa.Enum(name="issue_category").drop(bind, checkfirst=True)
    sa.Enum(name="issue_severity").drop(bind, checkfirst=True)
    sa.Enum(name="review_job_status").drop(bind, checkfirst=True)
    sa.Enum(name="repository_platform").drop(bind, checkfirst=True)
    sa.Enum(name="user_role").drop(bind, checkfirst=True)
