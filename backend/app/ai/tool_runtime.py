"""Runtime context shared by LangChain tools during one agent session."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review_job import ReviewJob


@dataclass(slots=True)
class AIToolRuntime:
    """Infrastructure handles hidden from public tool schemas."""

    job_id: UUID
    session_id: UUID
    sandbox_path: Path
    postgres_session: AsyncSession
    mongodb_database: AsyncIOMotorDatabase
    delivered_chunk_hashes: dict[tuple[str, int], str] = field(default_factory=dict)

    def register_chunk_content(
        self,
        *,
        file_path: str,
        chunk_index: int,
        content: str,
    ) -> tuple[bool, str, int]:
        """Track full chunks already delivered during this model session."""

        content_bytes = content.encode("utf-8")
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        chunk_key = (file_path, chunk_index)
        if self.delivered_chunk_hashes.get(chunk_key) == content_sha256:
            return False, content_sha256, len(content_bytes)

        self.delivered_chunk_hashes[chunk_key] = content_sha256
        return True, content_sha256, len(content_bytes)


_runtime: ContextVar[AIToolRuntime | None] = ContextVar(
    "ai_tool_runtime",
    default=None,
)


def get_ai_tool_runtime() -> AIToolRuntime:
    """Return the runtime context for the current AI tool call."""

    runtime = _runtime.get()
    if runtime is None:
        raise RuntimeError("AI tool runtime has not been configured")

    return runtime


async def ensure_ai_job_active() -> None:
    """Stop tool execution if the backing review job was canceled/deleted."""

    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.id).where(ReviewJob.id == runtime.job_id)
    )
    if result.scalar_one_or_none() is None:
        raise RuntimeError("Review job was canceled")


@contextmanager
def ai_tool_runtime(runtime: AIToolRuntime):
    """Bind runtime context for all tool calls in one agent execution."""

    token = _runtime.set(runtime)
    try:
        yield runtime
    finally:
        _runtime.reset(token)
