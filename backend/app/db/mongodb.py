"""MongoDB client setup for document and log storage."""

from collections.abc import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

from app.core.config import get_settings

FILE_ANALYSIS_RESULTS_COLLECTION = "file_analysis_results"
RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION = "raw_static_analysis_outputs"
TOOL_CALL_LOGS_COLLECTION = "tool_call_logs"
CHUNK_METADATA_COLLECTION = "chunk_metadata"
ROADMAP_COMPLIANCE_RESULTS_COLLECTION = "roadmap_compliance_results"

mongodb_client: AsyncIOMotorClient | None = None


def get_mongodb_client() -> AsyncIOMotorClient:
    """Return the shared MongoDB client for async document operations."""

    global mongodb_client

    if mongodb_client is None:
        settings = get_settings()
        mongodb_client = AsyncIOMotorClient(settings.mongodb_url)

    return mongodb_client


def get_mongodb_database() -> AsyncIOMotorDatabase:
    """Return the configured MongoDB database."""

    settings = get_settings()
    return get_mongodb_client()[settings.mongodb_db]


async def get_mongodb() -> AsyncIterator[AsyncIOMotorDatabase]:
    """Yield the shared MongoDB database for FastAPI dependencies."""

    yield get_mongodb_database()


async def ping_mongodb() -> None:
    """Verify MongoDB connectivity with a lightweight ping command."""

    await get_mongodb_database().command("ping")


async def ensure_mongodb_indexes() -> None:
    """Create MongoDB indexes required by current and planned query paths."""

    database = get_mongodb_database()

    await database[FILE_ANALYSIS_RESULTS_COLLECTION].create_index(
        [("job_id", ASCENDING)],
        name="idx_file_analysis_job",
    )
    await database[FILE_ANALYSIS_RESULTS_COLLECTION].create_index(
        [("job_id", ASCENDING), ("analyzed_at", ASCENDING)],
        name="idx_file_analysis_job_analyzed",
    )

    await database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION].create_index(
        [("job_id", ASCENDING)],
        name="idx_raw_static_job",
    )
    await database[RAW_STATIC_ANALYSIS_OUTPUTS_COLLECTION].create_index(
        [("job_id", ASCENDING), ("tool", ASCENDING), ("ran_at", ASCENDING)],
        name="idx_raw_static_job_tool_ran",
    )

    await database[TOOL_CALL_LOGS_COLLECTION].create_index(
        [("job_id", ASCENDING)],
        name="idx_tool_logs_job",
    )
    await database[TOOL_CALL_LOGS_COLLECTION].create_index(
        [("job_id", ASCENDING), ("session_id", ASCENDING), ("sequence", ASCENDING)],
        name="idx_tool_logs_job_session_sequence",
    )
    await database[TOOL_CALL_LOGS_COLLECTION].create_index(
        [("job_id", ASCENDING), ("called_at", ASCENDING)],
        name="idx_tool_logs_job_called",
    )
    await database[TOOL_CALL_LOGS_COLLECTION].create_index(
        [("called_at", ASCENDING)],
        name="idx_tool_logs_called",
    )

    await database[CHUNK_METADATA_COLLECTION].create_index(
        [("job_id", ASCENDING)],
        name="idx_chunk_metadata_job",
    )
    await database[CHUNK_METADATA_COLLECTION].create_index(
        [("job_id", ASCENDING), ("file_path", ASCENDING), ("chunk_index", ASCENDING)],
        name="idx_chunk_metadata_job_file_chunk",
    )
    await database[CHUNK_METADATA_COLLECTION].create_index(
        [("job_id", ASCENDING), ("module", ASCENDING), ("risk_area", ASCENDING)],
        name="idx_chunk_metadata_job_module_risk",
    )

    await database[ROADMAP_COMPLIANCE_RESULTS_COLLECTION].create_index(
        [("job_id", ASCENDING)],
        name="idx_roadmap_compliance_job",
    )
    await database[ROADMAP_COMPLIANCE_RESULTS_COLLECTION].create_index(
        [("job_id", ASCENDING), ("checked_at", ASCENDING)],
        name="idx_roadmap_compliance_job_checked",
    )
    await database[ROADMAP_COMPLIANCE_RESULTS_COLLECTION].create_index(
        [("rule_profile.id", ASCENDING)],
        name="idx_roadmap_compliance_rule_profile_id",
    )


async def close_mongodb_client() -> None:
    """Close the shared MongoDB client during application shutdown."""

    global mongodb_client

    if mongodb_client is not None:
        mongodb_client.close()
        mongodb_client = None
