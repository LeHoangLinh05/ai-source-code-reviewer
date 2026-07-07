"""Main AI agent orchestration for repository review."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from langchain_core.callbacks import AsyncCallbackHandler
except ImportError:

    class AsyncCallbackHandler:  # type: ignore[no-redef]
        """Fallback base class when LangChain is not installed at app startup."""


from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from langchain_core.agents import AgentAction

from app.ai.prompts import FINAL_REPORT_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.models.review_issue import ReviewIssue
from app.repositories.mongodb_repository import ToolCallLogRepository
from app.schemas.mongodb import ToolCallLogDocument

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 1_500

REACT_PROMPT_SUFFIX_TEMPLATE = """

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
{final_answer_instruction}

Question: {input}
Thought:{agent_scratchpad}
"""


REVIEW_FINAL_ANSWER_INSTRUCTION = (
    "Final Answer: a concise review handoff after every chunk_review_plan target "
    "chunk has been read and all AI issues have been generated"
)
REPORT_FINAL_ANSWER_INSTRUCTION = (
    "Final Answer: a concise completion note after generate_final_report has "
    "returned status=created"
)


def build_react_prompt(
    *,
    system_prompt: str,
    final_answer_instruction: str,
) -> Any:
    """Build the ReAct prompt around the exact system prompt contract."""

    from langchain_core.prompts import PromptTemplate

    return PromptTemplate.from_template(
        system_prompt
        + REACT_PROMPT_SUFFIX_TEMPLATE.format(
            final_answer_instruction=final_answer_instruction,
            input="{input}",
            agent_scratchpad="{agent_scratchpad}",
            tools="{tools}",
            tool_names="{tool_names}",
        )
    )


def create_review_agent_executor(llm: Any) -> Any:
    """Create the review-phase executor without the final report tool."""

    from app.ai.tools import AI_REVIEW_TOOLS

    return _create_agent_executor(
        llm=llm,
        tools=AI_REVIEW_TOOLS,
        system_prompt=REVIEW_SYSTEM_PROMPT,
        final_answer_instruction=REVIEW_FINAL_ANSWER_INSTRUCTION,
    )


def create_report_agent_executor(llm: Any) -> Any:
    """Create the report-phase executor that can only create the final report."""

    from app.ai.tools import AI_REPORT_TOOLS

    return _create_agent_executor(
        llm=llm,
        tools=AI_REPORT_TOOLS,
        system_prompt=FINAL_REPORT_SYSTEM_PROMPT,
        final_answer_instruction=REPORT_FINAL_ANSWER_INSTRUCTION,
    )


def _create_agent_executor(
    *,
    llm: Any,
    tools: list[Any],
    system_prompt: str,
    final_answer_instruction: str,
) -> Any:
    """Create a LangChain ReAct executor with a specific tool surface."""

    try:
        from langchain.agents import (  # type: ignore[attr-defined]
            AgentExecutor,
            create_react_agent,
        )
    except ImportError:
        from langchain_classic.agents import AgentExecutor, create_react_agent

    agent = create_react_agent(
        llm=llm,
        tools=tools,
        prompt=build_react_prompt(
            system_prompt=system_prompt,
            final_answer_instruction=final_answer_instruction,
        ),
        output_parser=MarkdownSafeReActOutputParser(),
    )
    return AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=MAX_AGENT_ITERATIONS,
        handle_parsing_errors=False,
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
        review_executor = create_review_agent_executor(llm)
        report_executor = create_report_agent_executor(llm)
        callback = MongoToolCallLogger(
            job_id=job_id,
            session_id=session_id,
            repository=ToolCallLogRepository(mongodb_database),
        )
        with ai_tool_runtime(runtime):
            review_result = await review_executor.ainvoke(
                {
                    "input": (
                        f"Review job_id={job_id}. Start by calling "
                        "analyze_project_structure with this job_id, use its "
                        "chunk_review_plan as the required target checklist, read "
                        "every required_chunk_indexes entry in that plan, generate "
                        "AI issues only when confidence >= 0.7, and then stop with "
                        "a review handoff summary. Do not create the final report "
                        "in this phase."
                    )
                },
                config={"callbacks": [callback]},
            )
            (
                reviewed_chunks,
                total_chunks,
                missing_chunks,
            ) = await _load_completed_review_coverage(job_id)
            report_context = await _build_report_context(
                job_id=job_id,
                postgres_session=postgres_session,
            )
            report_result = await report_executor.ainvoke(
                {
                    "input": (
                        f"Review job_id={job_id}. Target chunk coverage is complete "
                        f"({reviewed_chunks}/{total_chunks}). Now call "
                        "generate_final_report exactly once. Use this completed "
                        "review handoff and persisted issue context.\n\n"
                        f"Review handoff:\n{review_result.get('output', '')}\n\n"
                        f"Persisted issue context:\n{report_context}"
                    )
                },
                config={"callbacks": [callback]},
            )
        return {"review": dict(review_result), "report": dict(report_result)}

    return await run_with_llm_fallback(run_with_model, run_with_model)


async def _load_completed_review_coverage(
    job_id: UUID,
) -> tuple[int, int, list[dict[str, object]]]:
    from app.ai.tools.generate_report import _load_chunk_review_coverage

    reviewed_chunks, total_chunks, missing_chunks = await _load_chunk_review_coverage(
        job_id
    )
    if reviewed_chunks < total_chunks:
        raise RuntimeError(
            "AI review phase finished before target chunk coverage completed: "
            f"{reviewed_chunks}/{total_chunks} target chunks read; "
            f"missing preview={missing_chunks}"
        )

    return reviewed_chunks, total_chunks, missing_chunks


async def _build_report_context(
    *,
    job_id: UUID,
    postgres_session: AsyncSession,
) -> str:
    result = await postgres_session.execute(
        select(ReviewIssue).where(ReviewIssue.job_id == job_id)
    )
    issues = list(result.scalars().all())
    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for issue in issues:
        severity_counts[issue.severity.value] = (
            severity_counts.get(issue.severity.value, 0) + 1
        )
        category_counts[issue.category.value] = (
            category_counts.get(issue.category.value, 0) + 1
        )
        source_counts[issue.source.value] = source_counts.get(issue.source.value, 0) + 1

    top_issues = [
        {
            "severity": issue.severity.value,
            "category": issue.category.value,
            "source": issue.source.value,
            "title": issue.title,
            "file_path": issue.file_path,
            "line_start": issue.line_start,
        }
        for issue in issues[:20]
    ]
    return json.dumps(
        {
            "total_issues": len(issues),
            "severity_counts": severity_counts,
            "category_counts": category_counts,
            "source_counts": source_counts,
            "top_issues": top_issues,
        },
        ensure_ascii=False,
    )


def _get_react_output_parser_base() -> type[Any]:
    try:
        from langchain.agents.output_parsers.react_single_input import (  # type: ignore[import-not-found]
            ReActSingleInputOutputParser,
        )
    except ImportError:
        from langchain_classic.agents.output_parsers.react_single_input import (
            ReActSingleInputOutputParser,
        )

    return ReActSingleInputOutputParser


class MarkdownSafeReActOutputParser(_get_react_output_parser_base()):  # type: ignore[misc]
    """Normalize markdown-wrapped tool names before tool lookup."""

    def parse(self, text: str) -> Any:
        parsed_output = super().parse(text)
        if not isinstance(parsed_output, AgentAction):
            return parsed_output

        tool_name = normalize_tool_name(parsed_output.tool)
        if tool_name == parsed_output.tool:
            return parsed_output

        return AgentAction(
            tool=tool_name,
            tool_input=parsed_output.tool_input,
            log=parsed_output.log,
        )


def normalize_tool_name(tool_name: str) -> str:
    """Remove markdown/code quoting that LLMs sometimes add around Action names."""

    normalized_tool_name = tool_name.strip()
    normalized_tool_name = normalized_tool_name.strip("`")
    normalized_tool_name = normalized_tool_name.strip()
    normalized_tool_name = normalized_tool_name.strip("\"'")
    return normalized_tool_name.strip()


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
            _first_present_tool_input(kwargs, input_str),
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
        tool_input = _backfill_tool_input(
            tool_input=tool_input,
            tool_name=tool_name,
            output=output,
        )
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


def _first_present_tool_input(kwargs: dict[str, Any], input_str: str) -> Any:
    for key in ("inputs", "input", "tool_input"):
        value = kwargs.get(key)
        if value is not None:
            return value

    return input_str


def _backfill_tool_input(
    *,
    tool_input: dict[str, object],
    tool_name: str,
    output: dict[str, object],
) -> dict[str, object]:
    if tool_input and tool_input != {"input": None}:
        return tool_input

    if tool_name != "read_file_chunk":
        return tool_input

    inferred_input: dict[str, object] = {}
    for key in ("file_path", "chunk_index", "requested_chunk_index"):
        value = output.get(key)
        if value is not None:
            inferred_input[key] = value

    return inferred_input or tool_input


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
