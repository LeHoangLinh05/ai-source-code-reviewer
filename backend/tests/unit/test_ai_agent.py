"""Tests for AI agent executor configuration."""

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import (
    MAX_AGENT_ITERATIONS,
    MarkdownSafeReActOutputParser,
    _backfill_tool_input,
    create_report_agent_executor,
    create_review_agent_executor,
    normalize_tool_name,
)


def test_agent_executor_uses_review_iteration_budget() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert executor.max_iterations == MAX_AGENT_ITERATIONS


def test_review_executor_does_not_expose_final_report_tool() -> None:
    executor = create_review_agent_executor(FakeListChatModel(responses=[]))

    assert "generate_final_report" not in {tool.name for tool in executor.tools}


def test_report_executor_only_exposes_final_report_tool() -> None:
    executor = create_report_agent_executor(FakeListChatModel(responses=[]))

    assert {tool.name for tool in executor.tools} == {"generate_final_report"}


def test_normalize_tool_name_strips_markdown_backticks() -> None:
    assert normalize_tool_name("`read_file_chunk`") == "read_file_chunk"
    assert normalize_tool_name(" 'generate_issue' ") == "generate_issue"


def test_react_parser_strips_markdown_wrapped_action_name() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "Thought: read the file\n"
        "Action: `read_file_chunk`\n"
        'Action Input: {"file_path": "app.py", "chunk_index": 0}'
    )

    assert parsed.tool == "read_file_chunk"


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
