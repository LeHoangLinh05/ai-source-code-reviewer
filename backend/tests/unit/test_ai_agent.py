"""Tests for AI agent executor configuration."""

import hashlib
from typing import Any
from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import (
    MAX_AGENT_ITERATIONS,
    REPORT_AGENT_MAX_ITERATIONS,
    REVIEW_FINAL_ANSWER_INSTRUCTION,
    MarkdownSafeReActOutputParser,
    _backfill_tool_input,
    _build_review_pass_input,
    _roadmap_retry_instruction,
    _redact_source_tool_output,
    _run_review_until_coverage_complete,
    build_react_prompt,
    create_report_agent_executor,
    create_review_agent_executor,
    normalize_tool_name,
)
from app.ai.prompts import REVIEW_SYSTEM_PROMPT


def test_agent_executor_uses_review_iteration_budget() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert executor.max_iterations == MAX_AGENT_ITERATIONS


def test_agent_executor_does_not_feed_output_parsing_errors_back_to_agent() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert executor.handle_parsing_errors is False


def test_review_executor_does_not_expose_final_report_tool() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert "generate_final_report" not in {tool.name for tool in executor.tools}


def test_review_executor_exposes_ai_directed_chunk_reader() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))
    tool_names = {tool.name for tool in executor.tools}

    assert tool_names == {
        "analyze_project_structure",
        "read_file_chunk",
        "search_code_semantic",
        "search_knowledge_base",
        "generate_issue",
    }


def test_report_executor_only_exposes_final_report_tool() -> None:
    executor = create_report_agent_executor(FakeListChatModel(responses=[]))

    assert {tool.name for tool in executor.tools} == {"generate_final_report"}
    assert executor.max_iterations == REPORT_AGENT_MAX_ITERATIONS


def test_review_prompt_escapes_json_literals() -> None:
    prompt = build_react_prompt(
        system_prompt=REVIEW_SYSTEM_PROMPT,
        final_answer_instruction=REVIEW_FINAL_ANSWER_INSTRUCTION,
    )

    assert set(prompt.input_variables) == {
        "agent_scratchpad",
        "input",
        "tool_names",
        "tools",
    }
    assert '"file_path"' not in prompt.input_variables


def test_review_prompt_uses_semantic_content_without_rereading() -> None:
    assert "authoritative indexed chunk content" in REVIEW_SYSTEM_PROMPT
    assert "Do not call read_file_chunk merely" in (REVIEW_SYSTEM_PROMPT)
    assert "every applicable_rule_id" in REVIEW_SYSTEM_PROMPT
    assert "Silence is the correct" in REVIEW_SYSTEM_PROMPT
    assert 'source="KB"' in REVIEW_SYSTEM_PROMPT
    assert "status=deduplicated" in REVIEW_SYSTEM_PROMPT
    assert "do not" in REVIEW_SYSTEM_PROMPT
    assert "retrieve the same chunk" in REVIEW_SYSTEM_PROMPT


def test_normalize_tool_name_strips_markdown_backticks() -> None:
    assert normalize_tool_name("`read_file_chunk`") == "read_file_chunk"
    assert normalize_tool_name(" 'generate_issue' ") == "generate_issue"
    assert normalize_tool_name("generate_issue \n   -") == "generate_issue"
    assert normalize_tool_name("- generate_final_report") == "generate_final_report"


def test_react_parser_strips_markdown_wrapped_action_name() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "Thought: read the file\n"
        "Action: `read_file_chunk`\n"
        'Action Input: {"file_path": "app.py", "chunk_index": 0}'
    )

    assert parsed.tool == "read_file_chunk"


def test_react_parser_repairs_action_with_json_instead_of_tool_name() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "I will proceed to read the missing target chunks.\n"
        "Action: ```json\n"
        '{"file_path": "backend/app/ai/__init__.py", "chunk_index": 0}\n'
        "```"
    )

    assert parsed.tool == "read_file_chunk"
    assert parsed.tool_input == {
        "file_path": "backend/app/ai/__init__.py",
        "chunk_index": 0,
    }


def test_react_parser_repairs_final_report_json_action() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        'Action: {"executive_summary": "Done", "security_score": 8, "overall_score": 8}'
    )

    assert parsed.tool == "generate_final_report"


def test_react_parser_repairs_empty_json_project_structure_action() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "I will begin by analyzing the project structure to understand the files "
        "that need to be reviewed and their context. "
        "Action: ```json\n{}\n```"
    )

    assert parsed.tool == "analyze_project_structure"
    assert parsed.tool_input == {}


def test_react_parser_accepts_markdown_review_handoff_as_final_answer() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "### Review Handoff Summary\n"
        "#### Security Vulnerabilities\n"
        "No confirmed security issue met confidence threshold.\n"
        "### Suggested Scores\n"
        "- Security: 8/10"
    )

    assert parsed.return_values["output"].startswith("### Review Handoff Summary")


def test_react_parser_accepts_plain_text_handoff_as_final_answer() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse("Reviewed the target chunks. No confirmed issues found.")

    assert parsed.return_values["output"] == (
        "Reviewed the target chunks. No confirmed issues found."
    )


def test_react_parser_drains_distinct_multi_actions_then_finishes() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "**Action: generate_issue**\n**Action Input:**\n```json\n"
        '{"severity":"high","category":"bug","title":"First",'
        '"description":"First issue.","confidence":0.8,'
        '"path":"app.py","line_range":"1-2"}\n```\n'
        "**Observation:** generated\n**Action: generate_issue**\n"
        "**Action Input:**\n```json\n"
        '{"severity":"high","category":"bug","title":"Second",'
        '"description":"Second issue.","confidence":0.8,'
        '"path":"app.py","line_range":"3-4"}\n```\n'
        "### Final Review Handoff\nDone."
    )

    first = parser.parse(response)
    second = parser.parse(response)
    finished = parser.parse(response)

    assert first.tool_input["title"] == "First"
    assert second.tool_input["title"] == "Second"
    assert finished.return_values["output"] == "Done."


def test_react_parser_stops_fourth_attempt_for_same_issue() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "Thought: report issue\nAction: generate_issue\nAction Input: "
        '{"title":"Repeated","description":"Same issue.",'
        '"severity":"P0","category":"security",'
        '"file_path":"./app.py","line_start":1,"line_end":2,'
        '"confidence":0.9,"source":"KB"}'
    )

    first = parser.parse(response)
    second = parser.parse(response)
    third = parser.parse(response)
    stopped = parser.parse(response)

    assert first.tool == "generate_issue"
    assert second.tool == "generate_issue"
    assert third.tool == "generate_issue"
    assert "Stopped retrying" in stopped.return_values["output"]


def test_backfill_tool_input_from_read_file_output_when_callback_input_is_null() -> (
    None
):
    tool_input = _backfill_tool_input(
        tool_input={"input": None},
        tool_name="read_file_chunk",
        output={
            "status": "ok",
            "file_path": "Backend/app/api/deps.py",
            "chunk_index": 4,
        },
    )

    assert tool_input == {
        "file_path": "Backend/app/api/deps.py",
        "chunk_index": 4,
    }


def test_source_tool_trace_redacts_full_content() -> None:
    content = "def authenticate(token: str) -> bool:\n    return bool(token)\n"

    trace = _redact_source_tool_output(
        tool_name="search_code_semantic",
        output={
            "status": "ok",
            "results": [
                {
                    "file_path": "app/auth.py",
                    "chunk_index": 2,
                    "line_start": 40,
                    "line_end": 41,
                    "content": content,
                    "semantic_score": 0.91,
                }
            ],
        },
    )

    results = trace["results"]
    assert isinstance(results, list)
    result = results[0]
    assert isinstance(result, dict)
    assert "content" not in result
    assert result == {
        "status": "ok",
        "summary": "source content redacted from tool trace",
        "file_path": "app/auth.py",
        "chunk_index": 2,
        "line_start": 40,
        "line_end": 41,
        "chunk_key": ["app/auth.py", 2],
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "content_size": len(content.encode()),
    }


def test_initial_review_pass_input_starts_with_structure_analysis() -> None:
    prompt = _build_review_pass_input(
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        pass_number=1,
        reviewed_chunks=0,
        total_chunks=0,
        missing_chunks=[],
    )

    assert "Start by calling analyze_project_structure" in prompt
    assert "chunk_review_plan as an authoritative risk map" in prompt
    assert "semantic_audit_plan" in prompt
    assert "at most one search per semantic_audit_plan item" in prompt
    assert "search_code_semantic first" in prompt
    assert "read_file_chunk only as a narrow fallback" in prompt


def test_retry_review_pass_input_includes_missing_chunks() -> None:
    prompt = _build_review_pass_input(
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        pass_number=2,
        reviewed_chunks=26,
        total_chunks=185,
        missing_chunks=[
            {"file_path": "Backend/app/api/v1/chat.py", "chunk_index": 0},
        ],
    )

    assert "Previous review passes read 26/185 target chunks" in prompt
    assert "Do not bulk-read missing chunks" in prompt


def test_roadmap_retry_instructions_track_kb_evaluation() -> None:
    assert "not fully evaluated" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_submitted=False,
    )
    assert "already been retrieved" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_submitted=True,
    )
    assert "disabled" in _roadmap_retry_instruction(
        roadmap_required=False,
        roadmap_submitted=False,
    )


@pytest.mark.asyncio
async def test_review_loop_finishes_without_complete_chunk_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _RecordingReviewExecutor()
    coverage_results = [
        (
            1,
            2,
            [{"file_path": "Backend/app/api/v1/chat.py", "chunk_index": 0}],
        ),
        (2, 2, []),
    ]

    async def fake_load_review_coverage(
        job_id: UUID,
        *,
        missing_limit: int | None = 25,
    ) -> tuple[int, int, list[dict[str, object]]]:
        _ = job_id, missing_limit
        return coverage_results.pop(0)

    monkeypatch.setattr(
        "app.ai.agent._load_review_coverage",
        fake_load_review_coverage,
    )

    async def fake_roadmap_state(_job_id: UUID) -> tuple[bool, bool]:
        return False, False

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_submission_state",
        fake_roadmap_state,
    )

    callback: Any = object()
    result, reviewed_chunks, total_chunks = await _run_review_until_coverage_complete(
        review_executor=executor,
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        callback=callback,
    )

    assert reviewed_chunks == 1
    assert total_chunks == 2
    assert len(executor.inputs) == 1
    assert "Start by calling analyze_project_structure" in executor.inputs[0]
    assert result["output"] == "Pass 1: handoff 1"


@pytest.mark.asyncio
async def test_review_loop_does_not_persist_uncertain_kb_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _RecordingReviewExecutor()

    async def fake_load_review_coverage(
        job_id: UUID,
        *,
        missing_limit: int | None = 25,
    ) -> tuple[int, int, list[dict[str, object]]]:
        _ = job_id, missing_limit
        return 3, 88, [{"file_path": "backend/app/auth.py", "chunk_index": 1}]

    monkeypatch.setattr(
        "app.ai.agent._load_review_coverage",
        fake_load_review_coverage,
    )

    async def fake_roadmap_state(_job_id: UUID) -> tuple[bool, bool]:
        return True, False

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_submission_state",
        fake_roadmap_state,
    )

    job_id = UUID("00000000-0000-0000-0000-000000000001")
    result, reviewed_chunks, total_chunks = await _run_review_until_coverage_complete(
        review_executor=executor,
        job_id=job_id,
        callback=object(),
    )

    assert reviewed_chunks == 3
    assert total_chunks == 88
    assert "no issue was created without sufficient evidence" in result["output"]


class _RecordingReviewExecutor:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def ainvoke(
        self,
        payload: dict[str, str],
        *,
        config: dict[str, object],
    ) -> dict[str, str]:
        _ = config
        self.inputs.append(payload["input"])
        return {"output": f"handoff {len(self.inputs)}"}
