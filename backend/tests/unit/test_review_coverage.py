"""Tests for source coverage accounting."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.review.coverage import load_chunk_review_coverage
from app.ai.tools.runtime import AIToolRuntime, ai_tool_runtime
from app.db.mongodb import CHUNK_METADATA_COLLECTION, TOOL_CALL_LOGS_COLLECTION


@pytest.mark.asyncio
async def test_review_coverage_counts_probe_retrieval_chunks() -> None:
    job_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=Path("."),
        postgres_session=cast(AsyncSession, _FakePostgresSession()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(
                chunks=[
                    _chunk(job_id=job_id, file_path="app/a.py", chunk_index=0),
                    _chunk(job_id=job_id, file_path="app/a.py", chunk_index=1),
                    _chunk(job_id=job_id, file_path="app/b.py", chunk_index=0),
                ],
                trace_logs=[
                    {
                        "job_id": str(job_id),
                        "tool_name": "probe_retrieval",
                        "output": {
                            "status": "ok",
                            "results": [
                                {
                                    "file_path": "app/a.py",
                                    "chunk_index": 0,
                                },
                                {
                                    "file_path": "app/a.py",
                                    "chunk_index": 0,
                                },
                            ],
                        },
                    },
                    {
                        "job_id": str(job_id),
                        "tool_name": "unrelated_source_tool",
                        "output": {
                            "status": "ok",
                            "file_path": "app/a.py",
                            "chunk_index": 1,
                        },
                    },
                ],
            ),
        ),
    )

    with ai_tool_runtime(runtime):
        reviewed, total, missing = await load_chunk_review_coverage(job_id)

    assert reviewed == 1
    assert total == 3
    assert missing == [
        {"file_path": "app/a.py", "chunk_index": 1},
        {"file_path": "app/b.py", "chunk_index": 0},
    ]


def _chunk(*, job_id: UUID, file_path: str, chunk_index: int) -> dict[str, object]:
    return {
        "job_id": str(job_id),
        "file_path": file_path,
        "chunk_index": chunk_index,
        "line_start": chunk_index + 1,
        "line_end": chunk_index + 1,
    }


class _FakePostgresSession:
    async def execute(self, _statement: object) -> "_FakeScalarResult":
        return _FakeScalarResult()


class _FakeScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeMongoDatabase:
    def __init__(
        self,
        *,
        chunks: list[dict[str, object]],
        trace_logs: list[dict[str, object]],
    ) -> None:
        self.collections = {
            CHUNK_METADATA_COLLECTION: chunks,
            TOOL_CALL_LOGS_COLLECTION: trace_logs,
        }

    def __getitem__(self, name: str) -> "_FakeMongoCollection":
        return _FakeMongoCollection(self.collections.get(name, []))


class _FakeMongoCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self, query: dict[str, object]) -> "_FakeMongoCursor":
        return _FakeMongoCursor(
            [document for document in self.documents if _matches_query(document, query)]
        )


class _FakeMongoCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, object]]:
        _ = length
        return self.documents


def _matches_query(
    document: dict[str, object],
    query: dict[str, object],
) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            values = expected["$in"]
            if not isinstance(values, list) or document.get(key) not in values:
                return False
            continue
        if document.get(key) != expected:
            return False
    return True
