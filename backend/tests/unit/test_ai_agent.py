"""Tests for AI report agent orchestration helpers."""

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import (
    REPORT_AGENT_MAX_ITERATIONS,
    ROADMAP_RULE_CATALOG_TOOL_NAME,
    REPORT_FINAL_ANSWER_INSTRUCTION,
    MarkdownSafeReActOutputParser,
    _loaded_roadmap_rule_ids,
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


def test_react_parser_repairs_bare_final_report_tool_json() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse(
        "generate_final_report\n"
        "{\n"
        '  "executive_summary": "Done",\n'
        '  "security_score": 10,\n'
        '  "maintainability_score": 8,\n'
        '  "performance_score": 10,\n'
        '  "overall_score": 9,\n'
        '  "top_priorities": ["Clean up unused imports"],\n'
        '  "tech_stack": ["Python", "FastAPI"]\n'
        "}"
    )

    assert parsed.tool == "generate_final_report"
    assert parsed.tool_input["executive_summary"] == "Done"
    assert parsed.tool_input["tech_stack"] == ["Python", "FastAPI"]


def test_react_parser_accepts_plain_text_report_handoff_as_final_answer() -> None:
    parser = MarkdownSafeReActOutputParser()

    parsed = parser.parse("Final report generated successfully.")

    assert parsed.return_values["output"] == "Final report generated successfully."


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
