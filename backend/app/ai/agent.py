"""Main AI agent orchestration for repository review."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

try:
    from langchain_core.callbacks import AsyncCallbackHandler
except ImportError:
    class AsyncCallbackHandler:  # type: ignore[no-redef]
        """Fallback base class when LangChain is not installed at app startup."""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import REVIEW_SYSTEM_PROMPT
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.repositories.mongodb_repository import ToolCallLogRepository
from app.schemas.mongodb import ToolCallLogDocument

logger = logging.getLogger(__name__)

REACT_PROMPT_SUFFIX = """

You have access to the following tools:

{tools}

Use this format:

Question: the input task you must complete
Thought: think about what to do next
Action: the action to take, should be one of [{tool_names}]
Action Input: the JSON input to the action
Observation: the result of the action
... (repeat Thought/Action/Action Input/Observation as needed)
Thought: I now know the final answer
Final Answer: a concise completion note after generate_final_report has been called

Question: {input}
Thought:{agent_scratchpad}
"""


def build_react_prompt() -> Any:
    """Build the ReAct prompt around the exact system prompt contract."""

    from langchain_core.prompts import PromptTemplate

    return PromptTemplate.from_template(REVIEW_SYSTEM_PROMPT + REACT_PROMPT_SUFFIX)


def create_review_agent_executor(llm: Any) -> Any:
    """Create the LangChain ReAct executor with the hard iteration limit."""

    try:
        from langchain.agents import (  # type: ignore[attr-defined]
            AgentExecutor,
            create_react_agent,
        )
    except ImportError:
        from langchain_classic.agents import AgentExecutor, create_react_agent

    from app.ai.tools import AI_REVIEW_TOOLS

    agent = create_react_agent(
        llm=llm,
        tools=AI_REVIEW_TOOLS,
        prompt=build_react_prompt(),
    )
    return AgentExecutor(
        agent=agent,
        tools=AI_REVIEW_TOOLS,
        max_iterations=20,
        handle_parsing_errors=True,
        verbose=True,
    )


async def run_ai_review(
    *,
    job_id: UUID,
    sandbox_path: Path,
    postgres_session: AsyncSession,
    mongodb_database: AsyncIOMotorDatabase,
) -> dict[str, Any]:
    """Run the AI review agent for one job using primary LLM with fallback."""

    session_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=session_id,
        sandbox_path=sandbox_path,
        postgres_session=postgres_session,
        mongodb_database=mongodb_database,
    )

    from app.ai.llm_config import run_with_llm_fallback

    async def run_with_model(llm: Any) -> dict[str, Any]:
        executor = create_review_agent_executor(llm)

        callback = MongoToolCallLogger(
            job_id=job_id,
            session_id=session_id,
            repository=ToolCallLogRepository(mongodb_database),
        )
        with ai_tool_runtime(runtime):
            result = await executor.ainvoke(
                {
                    "input": (
                        f"Review job_id={job_id}. Start by calling "
                        "analyze_project_structure with this job_id, review all "
                        "high-priority files and every roadmap verification file, "
                        "then call generate_final_report."
                    )
                },
                config={"callbacks": [callback]},
            )
        return dict(result)

    return await run_with_llm_fallback(run_with_model, run_with_model)


class MongoToolCallLogger(AsyncCallbackHandler):
    """Persist all LangChain tool calls in MongoDB from one callback."""

    def __init__(
        self,
        *,
        job_id: UUID,
        session_id: UUID,
        repository: ToolCallLogRepository,
    ) -> None:
        self.job_id = job_id
        self.session_id = session_id
        self.repository = repository
        self.sequence = 0
        self.started_at_by_run_id: dict[UUID, datetime] = {}
        self.perf_start_by_run_id: dict[UUID, float] = {}
        self.input_by_run_id: dict[UUID, dict[str, object]] = {}
        self.tool_name_by_run_id: dict[UUID, str] = {}
        self.sequence_by_run_id: dict[UUID, int] = {}

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Remember tool inputs until LangChain emits the matching end event."""

        self.sequence += 1
        self.sequence_by_run_id[run_id] = self.sequence
        self.started_at_by_run_id[run_id] = datetime.now(UTC)
        self.perf_start_by_run_id[run_id] = time.perf_counter()
        self.input_by_run_id[run_id] = _normalize_tool_input(
            kwargs.get("inputs", input_str)
        )
        self.tool_name_by_run_id[run_id] = str(serialized.get("name", "unknown"))

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Write one successful tool trace."""

        _ = kwargs
        await self._write_log(
            run_id=run_id,
            output=_normalize_tool_output(output),
        )

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Write failed tool traces too so /ai-debug can show validation rejects."""

        _ = kwargs
        await self._write_log(
            run_id=run_id,
            output={"error": str(error), "error_type": type(error).__name__},
        )

    async def _write_log(
        self,
        *,
        run_id: UUID,
        output: dict[str, object],
    ) -> None:
        started_at = self.started_at_by_run_id.pop(run_id, datetime.now(UTC))
        perf_started_at = self.perf_start_by_run_id.pop(run_id, time.perf_counter())
        duration_ms = int((time.perf_counter() - perf_started_at) * 1000)
        tool_input = self.input_by_run_id.pop(run_id, {})
        tool_name = self.tool_name_by_run_id.pop(run_id, "unknown")
        sequence = self.sequence_by_run_id.pop(run_id, self.sequence)
        await self.repository.insert_one(
            ToolCallLogDocument(
                job_id=self.job_id,
                session_id=self.session_id,
                sequence=sequence,
                tool_name=tool_name,
                called_at=started_at,
                duration_ms=max(0, duration_ms),
                input=tool_input,
                output=output,
            )
        )


def _normalize_tool_input(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"input": value}
        if isinstance(parsed, dict):
            return _json_safe_dict(parsed)
        return {"input": parsed}

    return {"input": _json_safe(value)}


def _normalize_tool_output(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, BaseModel):
        return _json_safe_dict(value.model_dump(mode="json"))
    content = getattr(value, "content", None)
    if content is not None:
        return _normalize_tool_output(content)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"output": value}
        if isinstance(parsed, dict):
            return _json_safe_dict(parsed)
        return {"output": parsed}

    return {"output": _json_safe(value)}


def _json_safe_dict(value: dict[str, Any]) -> dict[str, object]:
    return {str(key): _json_safe(item) for key, item in value.items()}


def _json_safe(value: Any) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return _json_safe_dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    return str(value)
