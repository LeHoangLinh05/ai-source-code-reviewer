"""Tests for AI report agent orchestration helpers."""

import hashlib

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import (
    REPORT_AGENT_MAX_ITERATIONS,
    ROADMAP_RULE_CATALOG_TOOL_NAME,
    REPORT_FINAL_ANSWER_INSTRUCTION,
    MarkdownSafeReActOutputParser,
    _backfill_tool_input,
    _loaded_roadmap_rule_ids,
    _redact_source_tool_output,
    build_react_prompt,
    create_report_agent_executor,
    normalize_tool_name,
)
from app.ai.prompts import FINAL_REPORT_SYSTEM_PROMPT


def test_report_executor_only_exposes_final_report_tool() -> None:
    executor = create_report_agent_executor(FakeListChatModel(responses=[]))

    assert {tool.name for tool in executor.tools} == {"generate_final_report"}
    assert executor.max_iterations == REPORT_AGENT_MAX_ITERATIONS
    assert executor.handle_parsing_errors is False


def test_report_prompt_escapes_json_literals() -> None:
    prompt = build_react_prompt(
        system_prompt=FINAL_REPORT_SYSTEM_PROMPT,
        final_answer_instruction=REPORT_FINAL_ANSWER_INSTRUCTION,
    )

    assert set(prompt.input_variables) == {
        "agent_scratchpad",
        "input",
        "tool_names",
        "tools",
    }
    assert '"executive_summary"' not in prompt.input_variables


def test_final_report_prompt_limits_agent_to_report_generation() -> None:
    assert "call generate_final_report exactly once" in FINAL_REPORT_SYSTEM_PROMPT
    assert "Do not" in FINAL_REPORT_SYSTEM_PROMPT
    assert "generate new issues" in FINAL_REPORT_SYSTEM_PROMPT


def test_normalize_tool_name_strips_markdown_backticks() -> None:
    assert normalize_tool_name("`generate_final_report`") == "generate_final_report"
    assert normalize_tool_name(" 'generate_final_report' ") == "generate_final_report"
    assert normalize_tool_name("- generate_final_report") == "generate_final_report"


def test_react_parser_decodes_json_for_structured_report_arguments() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "Thought: create final report\n"
        "Action: generate_final_report\n"
        "Action Input: "
        '{"executive_summary":"Done","security_score":8,'
        '"maintainability_score":7,"performance_score":9,"overall_score":8}'
    )

    assert parsed.tool == "generate_final_report"
    assert parsed.tool_input == {
        "executive_summary": "Done",
        "security_score": 8,
        "maintainability_score": 7,
        "performance_score": 9,
        "overall_score": 8,
    }


def test_react_parser_repairs_final_report_json_action() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        'Action: {"executive_summary": "Done", "security_score": 8, "overall_score": 8}'
    )

    assert parsed.tool == "generate_final_report"
    assert parsed.tool_input["executive_summary"] == "Done"


def test_react_parser_accepts_plain_text_report_handoff_as_final_answer() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse("Final report generated successfully.")

    assert parsed.return_values["output"] == "Final report generated successfully."


def test_backfill_tool_input_from_legacy_read_file_output_when_input_is_null() -> None:
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


def test_legacy_source_tool_trace_redacts_full_content() -> None:
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
        "semantic_score": 0.91,
    }


def test_loaded_roadmap_rule_ids_include_catalog_trace() -> None:
    documents: list[object] = [
        {
            "tool_name": ROADMAP_RULE_CATALOG_TOOL_NAME,
            "output": {
                "status": "ok",
                "results": [
                    {
                        "metadata": {
                            "doc_type": "roadmap_rule",
                            "rule_id": "RC-W1-10",
                        }
                    },
                    {
                        "metadata": {
                            "doc_type": "roadmap_rule",
                            "rule_id": "RC-W1-11",
                        }
                    },
                ],
            },
        }
    ]

    assert _loaded_roadmap_rule_ids(documents) == {"RC-W1-10", "RC-W1-11"}
