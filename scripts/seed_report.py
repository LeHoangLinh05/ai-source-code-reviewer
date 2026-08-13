"""Seed a completed review job with realistic report and issue data.

Usage:
    python scripts/seed_report.py --job-id <uuid>
    python scripts/seed_report.py --job-id <uuid> --mark-completed

The generated records match the Reports API contract used by the frontend.
Phase 6 should write the same fields with real analyzer/AI output.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import delete, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db.postgres import AsyncSessionLocal  # noqa: E402
from app.models.job_status_history import JobStatusHistory  # noqa: E402
from app.models.review_issue import (  # noqa: E402
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.models.review_job import ReviewJob, ReviewJobStatus  # noqa: E402
from app.models.review_report import ReviewReport  # noqa: E402


@dataclass(frozen=True, slots=True)
class SeedIssue:
    """Static seed issue data used for frontend report development."""

    file_path: str
    line_start: int
    line_end: int
    severity: IssueSeverity
    category: IssueCategory
    title: str
    description: str
    suggestion: str
    source: IssueSource
    confidence: float
    rule_id: str


SEED_ISSUES: tuple[SeedIssue, ...] = (
    SeedIssue(
        "backend/app/routers/auth.py",
        42,
        47,
        IssueSeverity.CRITICAL,
        IssueCategory.SECURITY,
        "Potential SQL injection flow in auth lookup",
        "User-controlled email reaches a raw lookup path without clear parameter binding in the surrounding flow.",
        "Use SQLAlchemy parameterized statements and keep credential lookup inside repository methods.",
        IssueSource.AI_REVIEW,
        0.91,
        "AI-SQLI-001",
    ),
    SeedIssue(
        "backend/app/core/security.py",
        88,
        94,
        IssueSeverity.CRITICAL,
        IssueCategory.SECURITY,
        "Weak token validation fallback",
        "The token validation path accepts a broad exception branch that can hide malformed token handling.",
        "Catch JWT errors explicitly and return a stable authentication error.",
        IssueSource.AI_REVIEW,
        0.88,
        "AI-AUTH-002",
    ),
    SeedIssue(
        "backend/app/services/report_service.py",
        33,
        39,
        IssueSeverity.HIGH,
        IssueCategory.BUG,
        "Missing report status context",
        "Report lookup can return a generic not-found message without telling the UI which job state blocked report generation.",
        "Include the current job status in the not-ready error detail.",
        IssueSource.AI_REVIEW,
        0.84,
        "AI-REPORT-003",
    ),
    SeedIssue(
        "backend/app/repositories/user_repository.py",
        27,
        31,
        IssueSeverity.HIGH,
        IssueCategory.MAINTAINABILITY,
        "Repository method commits inside create",
        "Committing inside repository methods makes multi-step service workflows harder to roll back consistently.",
        "Prefer staging with flush and committing in the service transaction boundary.",
        IssueSource.AI_REVIEW,
        0.79,
        "AI-TX-004",
    ),
    SeedIssue(
        "backend/app/core/config.py",
        18,
        24,
        IssueSeverity.HIGH,
        IssueCategory.SECURITY,
        "Empty JWT secret allowed in development config",
        "A blank JWT secret can accidentally leak into non-local environments and make token signing unsafe.",
        "Validate required secrets at startup outside local development.",
        IssueSource.BANDIT,
        0.86,
        "B105",
    ),
    SeedIssue(
        "frontend/src/lib/api.ts",
        52,
        68,
        IssueSeverity.MEDIUM,
        IssueCategory.BUG,
        "Refresh redirect can race concurrent requests",
        "Multiple 401 responses can still trigger user-visible redirects while refresh is in progress.",
        "Keep a single refresh promise and suppress duplicate redirects until it settles.",
        IssueSource.AI_REVIEW,
        0.76,
        "AI-AUTH-005",
    ),
    SeedIssue(
        "frontend/src/app/reviews/page.tsx",
        71,
        77,
        IssueSeverity.MEDIUM,
        IssueCategory.PERFORMANCE,
        "Job list renders full table without virtualization",
        "Large review histories will re-render every row on refresh and can become sluggish.",
        "Add pagination or windowing before showing hundreds of jobs.",
        IssueSource.AI_REVIEW,
        0.72,
        "AI-FE-006",
    ),
    SeedIssue(
        "backend/app/services/review_jobs/service.py",
        55,
        62,
        IssueSeverity.MEDIUM,
        IssueCategory.MAINTAINABILITY,
        "Queue dependency is still a stub",
        "The queue service preserves the call site but does not supervise background execution yet.",
        "Replace the stub with Celery enqueue in P1.17 and cover worker error paths.",
        IssueSource.AI_REVIEW,
        0.81,
        "AI-QUEUE-007",
    ),
    SeedIssue(
        "backend/app/routers/repositories.py",
        29,
        36,
        IssueSeverity.MEDIUM,
        IssueCategory.STYLE,
        "Route returns manual model validation",
        "The route converts ORM objects manually even though FastAPI response_model can serialize from attributes.",
        "Keep explicit conversion only where response fields differ from the model.",
        IssueSource.RUFF,
        0.7,
        "SIM101",
    ),
    SeedIssue(
        "backend/app/repositories/report_repository.py",
        51,
        62,
        IssueSeverity.MEDIUM,
        IssueCategory.PERFORMANCE,
        "Issue count query rebuilds filter subquery",
        "The count path creates a subquery for simple filters, which is acceptable now but can become expensive.",
        "Monitor query plans and add targeted indexes for source/file_path search if needed.",
        IssueSource.AI_REVIEW,
        0.74,
        "AI-DB-008",
    ),
    SeedIssue(
        "frontend/src/app/repositories/[id]/page.tsx",
        111,
        145,
        IssueSeverity.LOW,
        IssueCategory.MAINTAINABILITY,
        "Start review modal lives inside page component",
        "The page is growing as slices add behavior, which can make future review history UI harder to read.",
        "Extract the modal once the review flow stabilizes.",
        IssueSource.AI_REVIEW,
        0.68,
        "AI-FE-009",
    ),
    SeedIssue(
        "backend/app/models/review_issue.py",
        45,
        61,
        IssueSeverity.LOW,
        IssueCategory.STYLE,
        "Enum definitions are duplicated in frontend types",
        "Backend enum values are mirrored manually in TypeScript and can drift as sources/categories grow.",
        "Generate API types from OpenAPI when the contract settles.",
        IssueSource.RUFF,
        0.66,
        "UP007",
    ),
    SeedIssue(
        "backend/app/analyzers/secret_scanner.py",
        18,
        29,
        IssueSeverity.LOW,
        IssueCategory.SECURITY,
        "Secret scanner needs entropy checks",
        "Regex-only detection can create false positives and miss encoded secrets.",
        "Add entropy scoring and provider-specific patterns in P1.16.",
        IssueSource.BANDIT,
        0.64,
        "B106",
    ),
    SeedIssue(
        "frontend/src/store/slices/filterSlice.ts",
        13,
        30,
        IssueSeverity.LOW,
        IssueCategory.MAINTAINABILITY,
        "Filter state is scoped to one report view",
        "The current issue filter state is enough for Slice 3 but may need namespacing for compare views.",
        "Reset filters on route change or namespace by job id when compare/export features arrive.",
        IssueSource.AI_REVIEW,
        0.67,
        "AI-FILTER-010",
    ),
    SeedIssue(
        "backend/app/db/postgres.py",
        19,
        23,
        IssueSeverity.INFO,
        IssueCategory.PERFORMANCE,
        "Connection pool settings rely on defaults",
        "The async engine uses SQLAlchemy defaults, which are fine locally but should be tuned for Docker deployment.",
        "Set pool size and overflow explicitly during P3.13 connection pooling work.",
        IssueSource.AI_REVIEW,
        0.6,
        "AI-DB-011",
    ),
    SeedIssue(
        "frontend/src/app/reviews/[id]/page.tsx",
        58,
        70,
        IssueSeverity.INFO,
        IssueCategory.MAINTAINABILITY,
        "Polling is temporary",
        "Polling keeps the UI testable before SSE, but it should not become the final realtime implementation.",
        "Replace with useJobProgress once Phase 5 SSE is implemented.",
        IssueSource.AI_REVIEW,
        0.59,
        "AI-SSE-012",
    ),
    SeedIssue(
        "backend/app/routers/reports.py",
        53,
        68,
        IssueSeverity.INFO,
        IssueCategory.STYLE,
        "Query params expose raw enum values",
        "The API contract is explicit and usable, but docs could include examples for common filters.",
        "Add OpenAPI examples when report export and compare endpoints are added.",
        IssueSource.RUFF,
        0.58,
        "D401",
    ),
    SeedIssue(
        "frontend/src/lib/reports.ts",
        14,
        29,
        IssueSeverity.INFO,
        IssueCategory.MAINTAINABILITY,
        "Report API client is intentionally thin",
        "The thin client keeps request logic simple but does not centralize cache invalidation.",
        "Move to RTK Query only if duplicated loading/cache code appears in later slices.",
        IssueSource.AI_REVIEW,
        0.57,
        "AI-FE-013",
    ),
)


async def seed_report(job_id: UUID, *, mark_completed: bool) -> None:
    """Create a realistic report and issue set for one review job."""

    async with AsyncSessionLocal() as session:
        review_job = await session.get(ReviewJob, job_id)
        if review_job is None:
            raise ValueError(f"Review job not found: {job_id}")

        if mark_completed and review_job.status != ReviewJobStatus.COMPLETED:
            review_job.status = ReviewJobStatus.COMPLETED
            review_job.completed_at = datetime.now(UTC)
            session.add(
                JobStatusHistory(
                    job_id=review_job.id,
                    status=ReviewJobStatus.COMPLETED.value,
                    message="Seed report completed",
                    progress=100,
                )
            )

        if review_job.status != ReviewJobStatus.COMPLETED:
            raise ValueError(
                "Review job must be COMPLETED before seeding a report. "
                "Re-run with --mark-completed to update it for local demos."
            )

        await session.execute(delete(ReviewIssue).where(ReviewIssue.job_id == job_id))
        existing_report = await session.scalar(
            select(ReviewReport).where(ReviewReport.job_id == job_id)
        )
        if existing_report is not None:
            await session.delete(existing_report)
            await session.flush()

        severity_counts = {
            severity: sum(1 for issue in SEED_ISSUES if issue.severity == severity)
            for severity in IssueSeverity
        }
        top_risky_files = _build_top_risky_files()
        report = ReviewReport(
            job_id=job_id,
            total_files_analyzed=86,
            total_issues=len(SEED_ISSUES),
            critical_count=severity_counts[IssueSeverity.CRITICAL],
            high_count=severity_counts[IssueSeverity.HIGH],
            medium_count=severity_counts[IssueSeverity.MEDIUM],
            low_count=severity_counts[IssueSeverity.LOW],
            info_count=severity_counts[IssueSeverity.INFO],
            security_score=6.4,
            maintainability_score=7.2,
            performance_score=7.8,
            overall_score=6.9,
            tech_stack={
                "languages": ["python", "typescript"],
                "frameworks": ["fastapi", "nextjs", "sqlalchemy"],
                "tools": ["ruff", "bandit", "ai_review"],
            },
            top_risky_files=top_risky_files,
            executive_summary=(
                "Seeded report: the codebase is functional and follows the planned "
                "API/service/repository layering, but security-sensitive auth and "
                "report flows still need worker-backed validation before production. "
                "Critical findings focus on token handling and raw data access, while "
                "medium findings highlight maintainability and performance risks."
            ),
            ai_model_used="seeded-contract-v1",
        )
        session.add(report)
        session.add_all(_build_issue_models(job_id))
        await session.commit()


def _build_top_risky_files() -> list[dict[str, object]]:
    issue_counts: dict[str, int] = {}
    max_severity: dict[str, str] = {}
    for issue in SEED_ISSUES:
        issue_counts[issue.file_path] = issue_counts.get(issue.file_path, 0) + 1
        max_severity.setdefault(issue.file_path, issue.severity.value)

    return [
        {
            "path": file_path,
            "issue_count": issue_count,
            "max_severity": max_severity[file_path],
        }
        for file_path, issue_count in sorted(
            issue_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    ]


def _build_issue_models(job_id: UUID) -> list[ReviewIssue]:
    return [
        ReviewIssue(
            job_id=job_id,
            file_path=issue.file_path,
            line_start=issue.line_start,
            line_end=issue.line_end,
            severity=issue.severity,
            category=issue.category,
            title=issue.title,
            description=issue.description,
            suggestion=issue.suggestion,
            source=issue.source,
            confidence=issue.confidence,
            raw_output={
                "rule_id": issue.rule_id,
                "seed": True,
                "contract_version": "report-seed-v1",
            },
        )
        for issue in SEED_ISSUES
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed fake report data for a job.")
    parser.add_argument("--job-id", required=True, type=UUID)
    parser.add_argument(
        "--mark-completed",
        action="store_true",
        help="Mark the job COMPLETED before inserting report data.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    await seed_report(args.job_id, mark_completed=args.mark_completed)
    print(f"Seeded report contract data for job {args.job_id}")


if __name__ == "__main__":
    asyncio.run(main())
