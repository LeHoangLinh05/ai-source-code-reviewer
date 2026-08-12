"""Evaluate a persisted fix job against executable fix-contract expectations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_MANIFEST = BACKEND_DIR / "benchmarks" / "fix_pipeline" / "mvp_inventory.json"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix-job-id", required=True, type=UUID)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    passed = asyncio.run(
        run_benchmark(fix_job_id=args.fix_job_id, manifest_path=args.manifest)
    )
    raise SystemExit(0 if passed else 1)


async def run_benchmark(*, fix_job_id: UUID, manifest_path: Path) -> bool:
    from app.db.postgres import AsyncSessionLocal, close_postgres_engine
    from app.repositories.fix_job_repository import FixJobRepository
    from benchmarks.fix_pipeline.evaluator import evaluate_fix_job

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        async with AsyncSessionLocal() as session:
            fix_job = await FixJobRepository(session).get_by_id(fix_job_id)
            if fix_job is None:
                raise ValueError(f"Fix job was not found: {fix_job_id}")
            evaluation = evaluate_fix_job(fix_job, manifest)
    finally:
        await close_postgres_engine()

    print(json.dumps(evaluation, indent=2, sort_keys=True))
    return bool(evaluation["passed"])


if __name__ == "__main__":
    main()
