"""MongoDB repository helpers for analysis documents and AI traces."""

from typing import Generic, TypeVar, cast
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pydantic import BaseModel

from app.db.mongodb import (
    CHUNK_METADATA_COLLECTION,
    FILE_ANALYSIS_RESULTS_COLLECTION,
    RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION,
    ROADMAP_COMPLIANCE_RESULTS_COLLECTION,
    TOOL_CALL_LOGS_COLLECTION,
)
from app.schemas.mongodb import (
    ChunkMetadataDocument,
    FileAnalysisResultDocument,
    RawStaticAnalysisOutputDocument,
    RoadmapComplianceResultDocument,
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


class ChunkMetadataRepository(MongoDocumentRepository[ChunkMetadataDocument]):
    """MongoDB access for code chunk metadata."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, CHUNK_METADATA_COLLECTION)


class RoadmapComplianceResultRepository(
    MongoDocumentRepository[RoadmapComplianceResultDocument]
):
    """MongoDB access for deterministic roadmap compliance results."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        super().__init__(database, ROADMAP_COMPLIANCE_RESULTS_COLLECTION)
