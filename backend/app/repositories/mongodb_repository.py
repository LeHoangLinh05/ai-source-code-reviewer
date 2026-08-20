"""MongoDB repository helpers for analysis documents and AI traces."""

from collections.abc import Collection
from typing import Generic, TypeVar, cast
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pydantic import BaseModel

from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    CODE_INDEX_MANIFESTS_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
    REPO_SUMMARY_RESULTS_COLLECTION,
    TOOL_CALL_LOGS_COLLECTION,
)
from app.schemas.mongodb import (
    ChunkMetadataDocument,
    CodeIndexManifestDocument,
    FileAnalysisResultDocument,
    RawStaticAnalysisOutputDocument,
    RepoSummaryResultDocument,
    ToolCallLogDocument,
)

DocumentT = TypeVar("DocumentT", bound=BaseModel)


class MongoDocumentRepository(Generic[DocumentT]):
    """Small typed helper for append/query MongoDB collections."""

    def __init__(
        self,
        database: AsyncIOMotorDatabase,
        collection_name: str,
    ) -> None:
        self.collection: AsyncIOMotorCollection = database[collection_name]

    async def insert_one(self, document: DocumentT) -> str:
        """Validate and insert one document, returning its MongoDB id."""

        payload = document.model_dump(mode="json")
        result = await self.collection.insert_one(payload)
        return str(result.inserted_id)

    async def insert_many(self, documents: list[DocumentT]) -> int:
        """Validate and insert many documents, returning the inserted count."""

        if not documents:
            return 0

        payloads = [document.model_dump(mode="json") for document in documents]
        result = await self.collection.insert_many(payloads, ordered=True)
        return len(result.inserted_ids)

    async def find_by_job_id(self, job_id: UUID) -> list[dict[str, object]]:
        """Return all documents linked to a PostgreSQL review job."""

        cursor = self.collection.find({"job_id": str(job_id)})
        documents = cast(list[dict[str, object]], await cursor.to_list(length=None))
        return [self._normalize_mongo_id(document) for document in documents]

    def _normalize_mongo_id(self, document: dict[str, object]) -> dict[str, object]:
        mongo_id = document.get("_id")
        if mongo_id is not None:
            document["_id"] = str(mongo_id)

        return document


class FileAnalysisResultRepository(MongoDocumentRepository[FileAnalysisResultDocument]):
    """MongoDB access for project structure analysis results."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, FILE_ANALYSIS_RESULTS_COLLECTION)

    async def find_latest_by_job_id(
        self,
        job_id: UUID,
    ) -> dict[str, object] | None:
        """Return the newest structure analysis document for a review job."""

        document = await self.collection.find_one(
            {"job_id": str(job_id)},
            sort=[("analyzed_at", -1)],
        )
        if document is None:
            return None
        return self._normalize_mongo_id(cast(dict[str, object], document))


class RawStaticAnalysisOutputRepository(
    MongoDocumentRepository[RawStaticAnalysisOutputDocument]
):
    """MongoDB access for raw static analyzer outputs."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION)


class ToolCallLogRepository(MongoDocumentRepository[ToolCallLogDocument]):
    """MongoDB access for AI agent tool call traces."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, TOOL_CALL_LOGS_COLLECTION)

    async def count_tool_events(self, job_id: UUID) -> int:
        """Count tool events, including legacy logs without an event type."""

        return int(
            await self.collection.count_documents(
                {
                    "job_id": str(job_id),
                    "$or": [
                        {"event_type": "tool"},
                        {"event_type": {"$exists": False}},
                    ],
                }
            )
        )

    async def find_trace_events(
        self,
        job_id: UUID,
    ) -> list[dict[str, object]]:
        """Return trace events in their persisted execution order."""

        cursor = self.collection.find({"job_id": str(job_id)}).sort(
            [("called_at", 1), ("sequence", 1)]
        )
        documents = cast(
            list[dict[str, object]],
            await cursor.to_list(length=None),
        )
        return [self._normalize_mongo_id(document) for document in documents]

    async def find_source_reads(
        self,
        *,
        job_id: UUID,
        tool_names: Collection[str],
    ) -> list[dict[str, object]]:
        """Return source-reading trace events used for coverage calculation."""

        cursor = self.collection.find(
            {
                "job_id": str(job_id),
                "tool_name": {"$in": sorted(tool_names)},
            }
        )
        documents = cast(
            list[dict[str, object]],
            await cursor.to_list(length=None),
        )
        return [self._normalize_mongo_id(document) for document in documents]


class ChunkMetadataRepository(MongoDocumentRepository[ChunkMetadataDocument]):
    """MongoDB access for code chunk metadata."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, CHUNK_METADATA_COLLECTION)

    async def replace_for_job(
        self,
        *,
        job_id: UUID,
        documents: list[ChunkMetadataDocument],
        batch_size: int,
    ) -> tuple[int, int]:
        """Replace chunk metadata for a job using bounded MongoDB batches."""

        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        await self.collection.delete_many({"job_id": str(job_id)})
        inserted_count = 0
        roundtrips = 0
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            inserted_count += await self.insert_many(batch)
            roundtrips += 1

        return inserted_count, roundtrips

    async def find_containing_line(
        self,
        *,
        job_id: UUID,
        file_path: str,
        line_start: int,
        line_end: int,
    ) -> dict[str, object] | None:
        """Return a persisted source chunk containing the requested line range."""

        document = await self.collection.find_one(
            {
                "job_id": str(job_id),
                "file_path": file_path,
                "line_start": {"$lte": line_start},
                "line_end": {"$gte": line_end},
            },
            sort=[("chunk_index", 1)],
        )
        if document is None:
            return None
        return self._normalize_mongo_id(cast(dict[str, object], document))

    async def find_containing_chunks(
        self,
        *,
        job_id: UUID,
        file_path: str,
        line_start: int,
        line_end: int,
    ) -> list[dict[str, object]]:
        """Return every persisted source chunk overlapping the requested line range.

        Unlike ``find_containing_line`` (which requires one chunk to fully
        contain the whole range), this returns all chunks that share any line
        with the requested range, so multi-chunk findings can still render a
        continuous code snippet.
        """

        cursor = self.collection.find(
            {
                "job_id": str(job_id),
                "file_path": file_path,
                "line_start": {"$lte": line_end},
                "line_end": {"$gte": line_start},
            }
        ).sort([("chunk_index", 1), ("line_start", 1)])
        documents = cast(
            list[dict[str, object]],
            await cursor.to_list(length=None),
        )
        return [self._normalize_mongo_id(document) for document in documents]


class CodeIndexManifestRepository(MongoDocumentRepository[CodeIndexManifestDocument]):
    """MongoDB access for immutable semantic index generation manifests."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, CODE_INDEX_MANIFESTS_COLLECTION)

    async def upsert_for_job(self, document: CodeIndexManifestDocument) -> None:
        """Create or replace the current lifecycle marker for one review job."""

        await self.collection.update_one(
            {"job_id": str(document.job_id)},
            {"$set": document.model_dump(mode="json")},
            upsert=True,
        )


class RepoSummaryResultRepository(MongoDocumentRepository[RepoSummaryResultDocument]):
    """MongoDB access for generated repository project overview summaries."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, REPO_SUMMARY_RESULTS_COLLECTION)

    async def find_latest_by_repository_id(
        self,
        repository_id: UUID,
    ) -> dict[str, object] | None:
        """Return the newest summary stored for a repository."""

        document = await self.collection.find_one(
            {"repository_id": str(repository_id)},
            sort=[("generated_at", -1)],
        )
        if document is None:
            return None
        return self._normalize_mongo_id(cast(dict[str, object], document))
