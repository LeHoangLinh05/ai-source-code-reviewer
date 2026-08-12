"""Optional fallback for simulating review job status transitions.

The main review flow now runs through the Celery process_review_job pipeline.
Keep this script only for frontend SSE/progress UI demos when the worker is not
running.

Usage:
    python scripts/simulate_job.py --job-id <uuid>
    python scripts/simulate_job.py --job-id <uuid> --delay 0.5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db.postgres import AsyncSessionLocal, close_postgres_engine  # noqa: E402
from app.db.redis import close_redis_client  # noqa: E402
from app.models.review_job import ReviewJobStatus  # noqa: E402
from app.repositories.review_job_repository import ReviewJobRepository  # noqa: E402
from app.services.notification_service import publish_job_progress  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimulatedStatus:
    """One status transition emitted by the simulation script."""

    status: ReviewJobStatus
    progress: int
    message: str


SIMULATED_STATUSES: tuple[SimulatedStatus, ...] = (
    SimulatedStatus(ReviewJobStatus.CLONING, 10, "Cloning repository"),
    SimulatedStatus(
        ReviewJobStatus.ANALYZING_STRUCTURE,
        25,
        "Analyzing project structure",
    ),
    SimulatedStatus(
        ReviewJobStatus.RUNNING_STATIC_ANALYSIS,
        40,
        "Running static analyzers",
    ),
    SimulatedStatus(ReviewJobStatus.CHUNKING_CODE, 55, "Chunking source code"),
    SimulatedStatus(ReviewJobStatus.AI_REVIEWING, 75, "Running AI review"),
    SimulatedStatus(ReviewJobStatus.GENERATING_REPORT, 90, "Generating report"),
    SimulatedStatus(ReviewJobStatus.COMPLETED, 100, "Review complete"),
)


async def simulate_job(job_id: UUID, delay_seconds: float) -> None:
    """Persist status changes and publish matching Redis progress events."""

    async with AsyncSessionLocal() as session:
        review_job_repository = ReviewJobRepository(session)
        review_job = await review_job_repository.get_by_id(job_id)
        if review_job is None:
            raise ValueError(f"Review job not found: {job_id}")

        for simulated_status in SIMULATED_STATUSES:
            review_job = await review_job_repository.update_status(
                review_job,
                status=simulated_status.status,
                message=simulated_status.message,
                progress=simulated_status.progress,
            )
            event_type = get_event_type(simulated_status.status)
            await publish_job_progress(
                job_id,
                event_type,
                {
                    "status": simulated_status.status.value,
                    "progress": simulated_status.progress,
                    "message": simulated_status.message,
                },
            )
            logger.info(
                "Published %s for job %s at %s%%",
                simulated_status.status.value,
                job_id,
                simulated_status.progress,
            )
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)


def get_event_type(status: ReviewJobStatus) -> str:
    """Map job status to the Phase 5 progress event name."""

    if status == ReviewJobStatus.COMPLETED:
        return "completed"

    if status == ReviewJobStatus.FAILED:
        return "failed"

    return "status_change"


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for job progress simulation."""

    parser = argparse.ArgumentParser(description="Simulate review job progress.")
    parser.add_argument("--job-id", required=True, type=UUID, help="Review job UUID")
    parser.add_argument(
        "--delay",
        default=0.0,
        type=float,
        help="Delay in seconds between status transitions",
    )
    return parser.parse_args()


async def main() -> None:
    """Run the status simulation."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    try:
        await simulate_job(args.job_id, args.delay)
    finally:
        await close_redis_client()
        await close_postgres_engine()


if __name__ == "__main__":
    asyncio.run(main())
