"""Runtime context shared by LangChain tools during one agent session."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class AIToolRuntime:
    """Infrastructure handles hidden from public tool schemas."""

    job_id: UUID
    session_id: UUID
    sandbox_path: Path
    postgres_session: AsyncSession
    mongodb_database: AsyncIOMotorDatabase


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


@contextmanager
def ai_tool_runtime(runtime: AIToolRuntime):
    """Bind runtime context for all tool calls in one agent execution."""

    token = _runtime.set(runtime)
    try:
        yield runtime
    finally:
        _runtime.reset(token)
