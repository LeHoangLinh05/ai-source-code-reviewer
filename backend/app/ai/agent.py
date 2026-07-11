"""Main AI agent orchestration for repository review."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from langchain_core.callbacks import AsyncCallbackHandler
except ImportError:

    class AsyncCallbackHandler:  # type: ignore[no-redef]
        """Fallback base class when LangChain is not installed at app startup."""


from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, PrivateAttr
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException

from app.ai.prompts import FINAL_REPORT_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT
from app.ai.roadmap.selection import get_applicable_rule_ids, parse_roadmap_profile
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime, get_ai_tool_runtime
from app.db.mongodb import TOOL_CALL_LOGS_COLLECTION
from app.models.review_issue import ReviewIssue
from app.models.review_job import ReviewJob
from app.repositories.mongodb_repository import ToolCallLogRepository
from app.schemas.mongodb import ToolCallLogDocument

logger = logging.getLogger(__name__)

REVIEW_AGENT_MAX_ITERATIONS = 36
REPORT_AGENT_MAX_ITERATIONS = 6
MAX_AGENT_ITERATIONS = REVIEW_AGENT_MAX_ITERATIONS
MAX_REVIEW_COVERAGE_PASSES = 2
REVIEW_RETRY_MISSING_CHUNK_LIMIT = 60
REACT_PARSING_ERROR_OBSERVATION = (
    "Invalid ReAct format. Continue using exactly one of these forms: "
    'Action: tool_name plus Action Input: {"field": "value"}, or Final Answer: ... '
    "Do not call tools with an empty JSON object unless the tool schema allows it."
)
KNOWN_REACT_TOOL_NAMES = {
    "analyze_project_structure",
    "generate_final_report",
    "generate_issue",
    "read_file_chunk",
    "read_next_review_chunk",
    "search_code_semantic",
    "search_knowledge_base",
}

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
    "Final Answer: a concise review handoff after semantic code review, AI issue "
    "generation, and enabled KB audit checks have completed"
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
        max_iterations=REVIEW_AGENT_MAX_ITERATIONS,
    )


def create_report_agent_executor(llm: Any) -> Any:
    """Create the report-phase executor that can only create the final report."""

    from app.ai.tools import AI_REPORT_TOOLS

    return _create_agent_executor(
        llm=llm,
        tools=AI_REPORT_TOOLS,
        system_prompt=FINAL_REPORT_SYSTEM_PROMPT,
        final_answer_instruction=REPORT_FINAL_ANSWER_INSTRUCTION,
        max_iterations=REPORT_AGENT_MAX_ITERATIONS,
    )


def _create_agent_executor(
    *,
    llm: Any,
    tools: list[Any],
    system_prompt: str,
    final_answer_instruction: str,
    max_iterations: int,
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
        max_iterations=max_iterations,
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
    """Run the AI review agent for one job using OpenAI."""

    session_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=session_id,
        sandbox_path=sandbox_path,
        postgres_session=postgres_session,
        mongodb_database=mongodb_database,
    )

    from app.ai.llm_config import run_with_configured_llm

    async def run_with_model(llm: Any) -> dict[str, Any]:
        review_executor = create_review_agent_executor(llm)
        report_executor = create_report_agent_executor(llm)
        callback = MongoToolCallLogger(
            job_id=job_id,
            session_id=session_id,
            repository=ToolCallLogRepository(mongodb_database),
        )
        with ai_tool_runtime(runtime):
            (
                review_result,
                reviewed_chunks,
                total_chunks,
            ) = await _run_review_until_coverage_complete(
                review_executor=review_executor,
                job_id=job_id,
                callback=callback,
            )
            report_context = await _build_report_context(
                job_id=job_id,
                postgres_session=postgres_session,
            )
            callback.agent_type = "report"
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

    return await run_with_configured_llm(run_with_model)


async def _run_review_until_coverage_complete(
    *,
    review_executor: Any,
    job_id: UUID,
    callback: MongoToolCallLogger,
) -> tuple[dict[str, Any], int, int]:
    """Run bounded review passes until review and roadmap obligations complete."""

    handoff_parts: list[str] = []
    reviewed_chunks = 0
    total_chunks = 0
    missing_chunks: list[dict[str, object]] = []
    roadmap_required = False
    roadmap_evaluated = False

    for pass_number in range(1, MAX_REVIEW_COVERAGE_PASSES + 1):
        review_result = await review_executor.ainvoke(
            {
                "input": _build_review_pass_input(
                    job_id=job_id,
                    pass_number=pass_number,
                    reviewed_chunks=reviewed_chunks,
                    total_chunks=total_chunks,
                    missing_chunks=missing_chunks,
                    roadmap_required=roadmap_required,
                    roadmap_submitted=roadmap_evaluated,
                )
            },
            config={"callbacks": [callback]},
        )
        handoff = review_result.get("output")
        if isinstance(handoff, str) and handoff.strip():
            handoff_parts.append(f"Pass {pass_number}: {handoff.strip()}")

        reviewed_chunks, total_chunks, missing_chunks = await _load_review_coverage(
            job_id,
            missing_limit=REVIEW_RETRY_MISSING_CHUNK_LIMIT,
        )
        roadmap_required, roadmap_evaluated = await _load_roadmap_submission_state(
            job_id
        )
        if not roadmap_required or roadmap_evaluated:
            merged_result = dict(review_result)
            merged_result["output"] = "\n\n".join(handoff_parts)
            return merged_result, reviewed_chunks, total_chunks

    if roadmap_required and not roadmap_evaluated:
        handoff_parts.append(
            "Some enabled KB rules were not retrieved within the bounded review "
            "budget; no issue was created without sufficient evidence."
        )

    return (
        {
            "output": "\n\n".join(handoff_parts),
        },
        reviewed_chunks,
        total_chunks,
    )


def _build_review_pass_input(
    *,
    job_id: UUID,
    pass_number: int,
    reviewed_chunks: int,
    total_chunks: int,
    missing_chunks: list[dict[str, object]],
    roadmap_required: bool = False,
    roadmap_submitted: bool = False,
) -> str:
    if pass_number == 1:
        return (
            f"Review job_id={job_id}. Start by calling "
            "analyze_project_structure with Action Input: {}, use its "
            "chunk_review_plan as an authoritative risk map and valid "
            "path source, not as a mandatory read checklist. Review code with "
            "search_code_semantic first using semantic_audit_plan plus "
            "behavior-based, cross-file, and roadmap-driven queries. Use "
            "at most one search per semantic_audit_plan item unless a result "
            "points to a clearly related path that needs one focused follow-up. "
            "Use read_file_chunk only as a narrow fallback "
            "when semantic search is unavailable or exact parent/neighbor context "
            "is essential. "
            "Run the mandatory audit method from the system prompt and derive "
            "targeted queries from roadmap hints, manifests, static findings, "
            "high-risk paths, routes, and framework conventions. If "
            "roadmap.ai_verification_rules is present, prioritize those logic "
            "rules before generic KB rules and generate normal issues "
            "when source evidence shows fake, stubbed, incomplete, or "
            "static-only implementations. "
            "Generate AI issues only when confidence >= 0.7, "
            "using exact file_path and line ranges from successful "
            "source tool outputs. For violations grounded in knowledge_base, set "
            'source="KB" and choose the category by the real problem. Treat '
            "deduplicated source outputs as already "
            "available in your prior context; do not search or read the same chunk "
            "again. Do not re-read semantic results or invent paths. Then "
            "if analyze_project_structure returns a roadmap profile, retrieve and "
            "evaluate every applicable_rule_id as a KB audit checklist. Create a "
            "normal issue only for confident violations; insufficient evidence "
            "produces no issue. If it returns no profile, do not retrieve roadmap "
            "rules. Stop with a review handoff summary. Do not create the final "
            "report in this phase."
        )

    missing_json = json.dumps(missing_chunks, ensure_ascii=False)
    roadmap_instruction = _roadmap_retry_instruction(
        roadmap_required=roadmap_required,
        roadmap_submitted=roadmap_submitted,
    )
    return (
        f"Continue review job_id={job_id}. Previous review passes read "
        f"{reviewed_chunks}/{total_chunks} target chunks through source tools. "
        "Do not bulk-read missing chunks. Continue semantic search only where it "
        "is needed for evidence-backed code issues. Generate code issues only "
        f"when confidence >= 0.7. {roadmap_instruction} Do not create the final "
        "report in this phase. Optional unresolved target preview for path "
        f"context only: {missing_json}"
    )


def _roadmap_retry_instruction(
    *,
    roadmap_required: bool,
    roadmap_submitted: bool,
) -> str:
    if roadmap_submitted:
        return (
            "Every enabled KB rule has already been retrieved in this Review Agent "
            "session; continue only with remaining code evidence work."
        )
    if roadmap_required:
        return (
            "The additional KB checklist is enabled but not fully evaluated. "
            "Retrieve every "
            "applicable_rule_id from analyze_project_structure, retrieve details "
            "with doc_type=roadmap_rule and the active profile/weeks when needed, "
            "and do not guess profile_id, weeks, or rule ids. If the exact "
            "applicable_rule_ids are not visible in this pass context, call "
            "analyze_project_structure again first. Generate normal source=KB "
            "issues only for violations backed by code evidence and confidence "
            ">= 0.7. Missing or ambiguous evidence produces no issue."
        )
    return "The additional KB checklist is disabled; do not retrieve roadmap rules."


async def _load_roadmap_submission_state(job_id: UUID) -> tuple[bool, bool]:
    runtime = get_ai_tool_runtime()
    result = await runtime.postgres_session.execute(
        select(ReviewJob.options).where(ReviewJob.id == job_id)
    )
    options = result.scalar_one_or_none()
    profile = parse_roadmap_profile(options if isinstance(options, dict) else None)
    if profile is None:
        return False, False

    applicable_rule_ids = set(get_applicable_rule_ids(profile))
    documents = (
        await runtime.mongodb_database[TOOL_CALL_LOGS_COLLECTION]
        .find(
            {
                "job_id": str(job_id),
                "agent_type": "review",
                "tool_name": "search_knowledge_base",
                "output.status": "ok",
            }
        )
        .to_list(length=None)
    )
    evaluated_rule_ids = _evaluated_roadmap_rule_ids(documents)
    return True, applicable_rule_ids.issubset(evaluated_rule_ids)


def _evaluated_roadmap_rule_ids(documents: list[object]) -> set[str]:
    """Return roadmap rule ids retrieved from successful KB tool calls."""

    evaluated_rule_ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        output = document.get("output")
        if not isinstance(output, dict):
            continue
        results = output.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            metadata = result.get("metadata")
            if not isinstance(metadata, dict):
                continue
            rule_id = metadata.get("rule_id")
            if metadata.get("doc_type") == "roadmap_rule" and isinstance(rule_id, str):
                evaluated_rule_ids.add(rule_id)

    return evaluated_rule_ids


async def _load_review_coverage(
    job_id: UUID,
    *,
    missing_limit: int | None = 25,
) -> tuple[int, int, list[dict[str, object]]]:
    from app.ai.tools.generate_report import _load_chunk_review_coverage

    return await _load_chunk_review_coverage(job_id, missing_limit=missing_limit)


async def _load_completed_review_coverage(
    job_id: UUID,
) -> tuple[int, int, list[dict[str, object]]]:
    reviewed_chunks, total_chunks, missing_chunks = await _load_review_coverage(job_id)
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
    issues.sort(key=_persisted_issue_priority)
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


def _persisted_issue_priority(issue: ReviewIssue) -> tuple[int, int]:
    raw_output = issue.raw_output or {}
    if raw_output.get("priority") == "P0":
        category_priority = 0
    else:
        category_priority = {
            "security": 1,
            "bug": 2,
            "performance": 3,
            "maintainability": 4,
            "requirement": 5,
            "style": 6,
        }.get(issue.category.value, 7)

    severity_priority = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "info": 4,
    }[issue.severity.value]
    return category_priority, severity_priority


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
    """Normalize common LLM formatting drift before tool lookup."""

    _handled_multi_action_payloads: set[str] = PrivateAttr(default_factory=set)
    _issue_action_attempts: dict[str, int] = PrivateAttr(default_factory=dict)

    def parse(self, text: str) -> Any:
        multi_action = self._parse_multi_action_output(text)
        if multi_action is not None:
            return multi_action

        try:
            parsed_output = super().parse(text)
        except OutputParserException:
            malformed_action = _coerce_malformed_json_action(text)
            if malformed_action is not None:
                return malformed_action

            final_answer = _coerce_freeform_final_answer(text)
            if final_answer is None:
                raise

            return AgentFinish(return_values={"output": final_answer}, log=text)

        if not isinstance(parsed_output, AgentAction):
            return parsed_output

        tool_name = normalize_tool_name(parsed_output.tool)
        repeated_issue = self._stop_repeated_issue_action(
            tool_name=tool_name,
            tool_input=parsed_output.tool_input,
            text=text,
        )
        if repeated_issue is not None:
            return repeated_issue
        if tool_name == parsed_output.tool:
            return parsed_output

        return AgentAction(
            tool=tool_name,
            tool_input=parsed_output.tool_input,
            log=parsed_output.log,
        )

    def _parse_multi_action_output(self, text: str) -> AgentAction | AgentFinish | None:
        payloads = [
            payload
            for payload in _extract_json_objects(text)
            if _infer_tool_name_from_payload(payload, text=text) == "generate_issue"
        ]
        if len(payloads) < 2:
            return None

        for payload in payloads:
            fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if fingerprint in self._handled_multi_action_payloads:
                continue
            self._handled_multi_action_payloads.add(fingerprint)
            return AgentAction(tool="generate_issue", tool_input=payload, log=text)

        return AgentFinish(
            return_values={"output": _multi_action_handoff(text)},
            log=text,
        )

    def _stop_repeated_issue_action(
        self,
        *,
        tool_name: str,
        tool_input: object,
        text: str,
    ) -> AgentFinish | None:
        if tool_name != "generate_issue":
            return None

        if isinstance(tool_input, str):
            issue_payload = _parse_json_object_prefix(tool_input)
        elif isinstance(tool_input, dict):
            issue_payload = tool_input
        else:
            issue_payload = None
        if issue_payload is None:
            return None

        fingerprint = _issue_action_fingerprint(issue_payload)
        attempts = self._issue_action_attempts.get(fingerprint, 0)
        if attempts >= 3:
            return AgentFinish(
                return_values={
                    "output": (
                        "Stopped retrying the same issue after three attempts; "
                        "continuing would not add a distinct finding."
                    )
                },
                log=text,
            )
        self._issue_action_attempts[fingerprint] = attempts + 1
        return None


def _coerce_malformed_json_action(text: str) -> AgentAction | None:
    """Repair outputs where the model puts JSON directly after Action."""

    if "Action:" not in text:
        return None

    payload = _extract_first_json_object(text)
    if payload is None:
        return None

    tool_name = _infer_tool_name_from_payload(payload, text=text)
    if tool_name is None:
        return None

    return AgentAction(tool=tool_name, tool_input=payload, log=text)


def _extract_first_json_object(text: str) -> dict[str, object] | None:
    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced_match is not None:
        parsed_fenced = _parse_json_object_prefix(fenced_match.group(1))
        if parsed_fenced is not None:
            return parsed_fenced

    object_start = text.find("{")
    if object_start < 0:
        return None

    return _parse_json_object_prefix(text[object_start:])


def _extract_json_objects(text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        payload = _parse_json_object_prefix(match.group(1))
        if payload is not None:
            payloads.append(payload)
    return payloads


def _multi_action_handoff(text: str) -> str:
    for marker in ("### Final Review Handoff", "Final Answer:"):
        if marker in text:
            handoff = text.split(marker, 1)[1].strip()
            if handoff:
                return handoff
    return "Processed all distinct issue actions from the model response."


def _issue_action_fingerprint(payload: dict[str, object]) -> str:
    line_range = payload.get("line_range")
    location = (
        line_range
        if line_range is not None
        else (
            payload.get("line_start"),
            payload.get("line_end"),
        )
    )
    identifying_fields = {
        "title": payload.get("title"),
        "file_path": payload.get("file_path") or payload.get("path"),
        "location": location,
        "category": payload.get("category"),
    }
    return json.dumps(identifying_fields, sort_keys=True, ensure_ascii=False)


def _parse_json_object_prefix(text: str) -> dict[str, object] | None:
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text.strip())
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _infer_tool_name_from_payload(
    payload: dict[str, object],
    *,
    text: str,
) -> str | None:
    if "file_path" in payload:
        return "read_file_chunk"
    if "executive_summary" in payload or "overall_score" in payload:
        return "generate_final_report"
    if "severity" in payload or "category" in payload or "confidence" in payload:
        return "generate_issue"
    if "query" in payload and "job_id" in payload:
        return "search_code_semantic"
    if "query" in payload:
        return "search_knowledge_base"
    if not payload and _mentions_project_structure_analysis(text):
        return "analyze_project_structure"

    return None


def _mentions_project_structure_analysis(text: str) -> bool:
    normalized_text = text.lower().replace("_", " ")
    return "project structure" in normalized_text and any(
        verb in normalized_text for verb in ("analyze", "analyse", "understand")
    )


def _coerce_freeform_final_answer(text: str) -> str | None:
    """Accept markdown handoffs when the model omits the ReAct Final Answer label."""

    stripped_text = text.strip()
    if not stripped_text:
        return None
    if _contains_react_action(stripped_text):
        return None
    if stripped_text == REACT_PARSING_ERROR_OBSERVATION:
        return None

    final_answer_marker = "Final Answer:"
    if final_answer_marker in stripped_text:
        return stripped_text.split(final_answer_marker, 1)[1].strip() or None

    return stripped_text


def _contains_react_action(text: str) -> bool:
    lines = text.splitlines()
    return any(line.strip().startswith("Action:") for line in lines)


def normalize_tool_name(tool_name: str) -> str:
    """Remove markdown/code quoting that LLMs sometimes add around Action names."""

    normalized_tool_name = tool_name.strip()
    normalized_tool_name = normalized_tool_name.strip("`")
    normalized_tool_name = normalized_tool_name.strip()
    normalized_tool_name = normalized_tool_name.strip("\"'")
    normalized_tool_name = normalized_tool_name.strip()
    if normalized_tool_name in KNOWN_REACT_TOOL_NAMES:
        return normalized_tool_name

    for known_tool_name in KNOWN_REACT_TOOL_NAMES:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(known_tool_name)}(?![A-Za-z0-9_])"
        if re.search(pattern, normalized_tool_name):
            return known_tool_name

    return normalized_tool_name


class MongoToolCallLogger(AsyncCallbackHandler):
    """Persist all LangChain tool calls in MongoDB from one callback."""

    def __init__(
        self,
        *,
        job_id: UUID,
        session_id: UUID,
        repository: ToolCallLogRepository,
        agent_type: Literal["review", "report"] = "review",
    ) -> None:
        self.job_id = job_id
        self.session_id = session_id
        self.repository = repository
        self.agent_type = agent_type
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
        output = _redact_source_tool_output(tool_name=tool_name, output=output)
        sequence = self.sequence_by_run_id.pop(run_id, self.sequence)
        await self.repository.insert_one(
            ToolCallLogDocument(
                job_id=self.job_id,
                session_id=self.session_id,
                agent_type=self.agent_type,
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

    if tool_name not in {"read_file_chunk", "read_next_review_chunk"}:
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


def _redact_source_tool_output(
    *,
    tool_name: str,
    output: dict[str, object],
) -> dict[str, object]:
    if tool_name not in {
        "read_file_chunk",
        "read_next_review_chunk",
        "search_code_semantic",
    }:
        return output

    if tool_name == "search_code_semantic":
        raw_results = output.get("results")
        results = raw_results if isinstance(raw_results, list) else []
        return {
            "status": str(output.get("status", "unknown")),
            "summary": "source content redacted from tool trace",
            "result_count": len(results),
            "results": [
                _source_result_trace(result)
                for result in results
                if isinstance(result, dict)
            ],
        }

    return _source_result_trace(output)


def _source_result_trace(result: dict[str, object]) -> dict[str, object]:
    trace: dict[str, object] = {
        "status": str(result.get("status", "ok")),
        "summary": str(
            result.get("summary")
            or result.get("reason")
            or "source content redacted from tool trace"
        ),
    }
    for field in ("file_path", "chunk_index", "line_start", "line_end"):
        value = result.get(field)
        if value is not None:
            trace[field] = value

    file_path = result.get("file_path")
    chunk_index = result.get("chunk_index")
    if isinstance(file_path, str) and isinstance(chunk_index, int):
        trace["chunk_key"] = [file_path, chunk_index]

    content = result.get("content")
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
        trace["content_sha256"] = hashlib.sha256(content_bytes).hexdigest()
        trace["content_size"] = len(content_bytes)
    else:
        for field in ("content_sha256", "content_size"):
            value = result.get(field)
            if value is not None:
                trace[field] = value

    return trace


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
