"""Tests for AI agent executor configuration."""

import hashlib
from typing import Any
from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.ai.agent import (
    MAX_AGENT_ITERATIONS,
    REPORT_AGENT_MAX_ITERATIONS,
    ROADMAP_RULE_CATALOG_TOOL_NAME,
    REVIEW_FINAL_ANSWER_INSTRUCTION,
    MarkdownSafeReActOutputParser,
    _backfill_tool_input,
    _build_review_pass_input,
    _loaded_roadmap_rule_ids,
    _planned_roadmap_rule_ids,
    _planned_semantic_category_items,
    _roadmap_retry_instruction,
    _redact_source_tool_output,
    _run_review_until_coverage_complete,
    _searched_roadmap_rule_ids,
    _searched_semantic_audit_item_ids,
    _semantic_audit_retry_instruction,
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
        "search_code",
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


def test_review_prompt_uses_preview_first_semantic_search_policy() -> None:
    assert "unified search_code tool" in REVIEW_SYSTEM_PROMPT
    assert "preview_only" in REVIEW_SYSTEM_PROMPT
    assert "Do not automatically read" in REVIEW_SYSTEM_PROMPT
    assert "missing_behavior" in REVIEW_SYSTEM_PROMPT
    assert "auto-loaded roadmap catalog" in REVIEW_SYSTEM_PROMPT
    assert "unified probe plan" in REVIEW_SYSTEM_PROMPT
    assert "search unit" in REVIEW_SYSTEM_PROMPT
    assert "priority signal" in REVIEW_SYSTEM_PROMPT
    assert "Silence is the correct" in REVIEW_SYSTEM_PROMPT
    assert 'source="KB"' in REVIEW_SYSTEM_PROMPT
    assert "status=deduplicated" in REVIEW_SYSTEM_PROMPT
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


def test_react_parser_decodes_json_for_structured_tool_arguments() -> None:
    parser = MarkdownSafeReActOutputParser()
    query = (
        "Verify login behavior. Historical hints: "
        "{'glob': ['**/*.py'], 'regex': '/login'};"
    )

    parsed = parser.parse(
        "Thought: verify the requirement\n"
        "Action: search_code\n"
        "Action Input: "
        '{"query":"' + query + '","reason":"category_review",'
        '"top_k":5,"investigation_id":"auth-login"}'
    )

    assert parsed.tool == "search_code"
    assert parsed.tool_input == {
        "query": query,
        "reason": "category_review",
        "top_k": 5,
        "investigation_id": "auth-login",
    }


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


def test_react_parser_stops_second_incomplete_issue_attempt() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "Thought: report issue\nAction: generate_issue\nAction Input: "
        '{"investigation_id":"auto-login","claim_type":"present_defect",'
        '"supporting_evidence":[{"file_path":"backend/app/auth.py",'
        '"chunk_index":6,"line_start":26,"line_end":38,'
        '"rationale":"Hardcoded token."}],'
        '"contradicting_evidence":[],"coverage_summary":"Checked login."}'
    )

    first = parser.parse(response)
    stopped = parser.parse(response)

    assert first.tool == "generate_issue"
    assert "Stopped retrying" in stopped.return_values["output"]


def test_react_parser_stops_same_issue_with_changed_investigation_id() -> None:
    parser = MarkdownSafeReActOutputParser()

    def response(investigation_id: str) -> str:
        return (
            "Thought: report issue\nAction: generate_issue\nAction Input: "
            '{"investigation_id":"'
            + investigation_id
            + '","title":"Repeated","description":"Same issue.",'
            '"severity":"high","category":"security",'
            '"file_path":"app.py","line_start":1,"line_end":2,'
            '"confidence":0.8,"supporting_evidence":['
            '{"file_path":"app.py","chunk_index":0,"line_start":1,'
            '"line_end":2,"rationale":"same evidence"}]}'
        )

    first = parser.parse(response("claim-1"))
    second = parser.parse(response("claim-2"))
    third = parser.parse(response("claim-3"))
    stopped = parser.parse(response("claim-4"))

    assert first.tool == "generate_issue"
    assert second.tool == "generate_issue"
    assert third.tool == "generate_issue"
    assert "Stopped retrying" in stopped.return_values["output"]


def test_react_parser_allows_corrected_issue_payloads() -> None:
    parser = MarkdownSafeReActOutputParser()
    responses = [
        (
            "Thought: report issue\nAction: generate_issue\nAction Input: "
            '{"investigation_id":"auth","supporting_evidence":"source"}'
        ),
        (
            "Thought: correct evidence\nAction: generate_issue\nAction Input: "
            '{"investigation_id":"auth","supporting_evidence":["source"]}'
        ),
        (
            "Thought: complete evidence\nAction: generate_issue\nAction Input: "
            '{"investigation_id":"auth","supporting_evidence":['
            '{"file_path":"auth.py","chunk_index":1,"line_start":2,'
            '"line_end":3,"rationale":"fixed token"}]}'
        ),
        (
            "Thought: complete issue\nAction: generate_issue\nAction Input: "
            '{"investigation_id":"auth","title":"Fixed token",'
            '"category":"security","supporting_evidence":['
            '{"file_path":"auth.py","chunk_index":1,"line_start":2,'
            '"line_end":3,"rationale":"fixed token"}]}'
        ),
    ]

    parsed = [parser.parse(response) for response in responses]

    assert all(action.tool == "generate_issue" for action in parsed)


def test_react_parser_repairs_generate_issue_fields_split_outside_json() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "Thought: report issue\nAction: generate_issue\nAction Input: "
        '{"investigation_id":"RC-W1-11","claim_type":"present_defect",'
        '"supporting_evidence":[{"file_path":"backend/app/auth.py",'
        '"chunk_index":8,"line_start":42,"line_end":48,'
        '"rationale":"Token refresh is unconditional."}],'
        '"contradicting_evidence":[],"coverage_summary":"Checked route."},'
        '"severity":"high","category":"requirement",'
        '"title":"Refresh token is not validated",'
        '"description":"The route issues a token without validation.",'
        '"confidence":0.8,"file_path":"backend/app/auth.py",'
        '"line_start":42,"line_end":48'
    )

    parsed = parser.parse(response)

    assert parsed.tool == "generate_issue"
    assert parsed.tool_input["severity"] == "high"
    assert parsed.tool_input["title"] == "Refresh token is not validated"
    assert parsed.tool_input["file_path"] == "backend/app/auth.py"


def test_react_parser_repairs_split_generate_issue_with_closing_brace() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "Thought: report issue\nAction: generate_issue\nAction Input: "
        '{"investigation_id":"auto-review-backend/auth.py",'
        '"claim_type":"present_defect",'
        '"supporting_evidence":[{"file_path":"backend/app/auth.py",'
        '"chunk_index":2,"line_start":12,"line_end":14,'
        '"rationale":"UserLogin lacks password verification."}],'
        '"contradicting_evidence":[],"contradiction_resolution":"",'
        '"coverage_summary":"Checked auth route."},'
        '"severity":"high","category":"security",'
        '"title":"Incomplete Password Handling in UserLogin Class",'
        '"description":"The route accepts credentials without verification.",'
        '"confidence":0.8,"file_path":"backend/app/auth.py",'
        '"line_start":12,"line_end":14}'
    )

    parsed = parser.parse(response)

    assert parsed.tool == "generate_issue"
    assert parsed.tool_input["severity"] == "high"
    assert parsed.tool_input["category"] == "security"
    assert parsed.tool_input["confidence"] == 0.8
    assert parsed.tool_input["line_end"] == 14


def test_react_parser_stops_third_identical_search_code_action() -> None:
    parser = MarkdownSafeReActOutputParser()
    response = (
        "Thought: search realtime\nAction: search_code\nAction Input: "
        '{"query":"Review realtime category",'
        '"audit_plan_item_id":"category_review:realtime:1",'
        '"mode":"auto","top_k":5}'
    )

    first = parser.parse(response)
    second = parser.parse(response)
    third = parser.parse(response)

    assert first.tool == "search_code"
    assert second.tool == "search_code"
    assert (
        "Stopped repeating the same search_code action" in third.return_values["output"]
    )


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
        "semantic_score": 0.91,
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
    assert "audit_plan_item_id" in prompt
    assert "reason=category_probe" in prompt
    assert "at most one search per semantic_audit_plan item" in prompt
    assert "search_code mode='auto' first" in prompt
    assert "stable investigation_id" in prompt
    assert "Search previews are not evidence" in prompt


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


def test_semantic_audit_retry_instruction_lists_missing_items() -> None:
    instruction = _semantic_audit_retry_instruction(
        [
            "category_probe:security.jwt_session_auth",
            "category_probe:performance.n_plus_one",
        ]
    )

    assert "category_probe:security.jwt_session_auth" in instruction
    assert "audit_plan_item_id" in instruction
    assert "broad category-level search" in instruction


def test_roadmap_retry_instructions_track_catalog_load() -> None:
    assert "backend catalog loader" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_catalog_loaded=False,
        roadmap_missing_review_rule_ids=[],
    )
    assert "catalog is already loaded" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_catalog_loaded=True,
        roadmap_missing_review_rule_ids=[],
    )
    assert "RC-W3-09" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_catalog_loaded=True,
        roadmap_missing_review_rule_ids=["RC-W3-09"],
    )
    assert "Do not fall back to a broad category query" in _roadmap_retry_instruction(
        roadmap_required=True,
        roadmap_catalog_loaded=True,
        roadmap_missing_review_rule_ids=["RC-W3-09"],
    )
    assert "disabled" in _roadmap_retry_instruction(
        roadmap_required=False,
        roadmap_catalog_loaded=False,
        roadmap_missing_review_rule_ids=[],
    )


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


def test_planned_roadmap_rule_ids_include_category_review_trace() -> None:
    documents: list[object] = [
        {
            "tool_name": "analyze_project_structure",
            "output": {
                "semantic_audit_plan": [
                    {
                        "reason": "category_review",
                        "related_rule_ids": ["RC-W1-10", "rc-w1-11"],
                    },
                    {
                        "reason": "high_risk_file",
                        "related_rule_ids": ["RC-W9-99"],
                    },
                ]
            },
        }
    ]

    assert _planned_roadmap_rule_ids(documents) == {"RC-W1-10", "RC-W1-11"}


def test_semantic_category_plan_items_track_search_inputs() -> None:
    plan_documents: list[object] = [
        {
            "output": {
                "semantic_audit_plan": [
                    {
                        "audit_plan_item_id": "category_review:security:1",
                        "reason": "category_review",
                        "query": "Review security behavior",
                    },
                    {
                        "audit_plan_item_id": "category_review:performance:1",
                        "reason": "category_review",
                        "query": "Review performance behavior",
                    },
                    {
                        "audit_plan_item_id": "high_risk_file:auth",
                        "reason": "high_risk_file",
                        "query": "Review auth.py",
                    },
                ]
            },
        }
    ]
    planned_items = _planned_semantic_category_items(plan_documents)
    search_documents: list[object] = [
        {
            "input": {
                "audit_plan_item_id": "category_review:security:1",
                "query": "shorter security query",
            }
        },
        {
            "input": {
                "query": "Review performance behavior",
            }
        },
    ]

    assert planned_items == {
        "category_review:security:1": "review security behavior",
        "category_review:performance:1": "review performance behavior",
    }
    assert _searched_semantic_audit_item_ids(
        documents=search_documents,
        planned_items=planned_items,
    ) == {
        "category_review:security:1",
        "category_review:performance:1",
    }


def test_searched_roadmap_rule_ids_parse_search_inputs() -> None:
    documents: list[object] = [
        {
            "input": {
                "query": "Verify rule RC-W1-10 for login",
                "investigation_id": "auth-login",
            }
        },
        {
            "input": {
                "query": "Verify refresh behavior",
                "investigation_id": "auto-rc-w1-11",
            }
        },
        {
            "input": {
                "query": "Có thư viện JWT",
                "rule_id": "RC-W1-04",
            }
        },
    ]

    assert _searched_roadmap_rule_ids(documents) == {
        "RC-W1-04",
        "RC-W1-10",
        "RC-W1-11",
    }


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
        "app.ai.agent._load_roadmap_catalog_state",
        fake_roadmap_state,
    )

    async def fake_semantic_audit_coverage(_job_id: UUID) -> list[str]:
        return []

    monkeypatch.setattr(
        "app.ai.agent._load_semantic_audit_plan_coverage",
        fake_semantic_audit_coverage,
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

    roadmap_states = [(True, False), (True, True), (True, True)]

    async def fake_roadmap_state(_job_id: UUID) -> tuple[bool, bool]:
        return roadmap_states.pop(0)

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_catalog_state",
        fake_roadmap_state,
    )

    async def fake_roadmap_review_state(_job_id: UUID) -> tuple[bool, list[str]]:
        return True, []

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_review_rule_state",
        fake_roadmap_review_state,
    )

    async def fake_semantic_audit_coverage(_job_id: UUID) -> list[str]:
        return []

    monkeypatch.setattr(
        "app.ai.agent._load_semantic_audit_plan_coverage",
        fake_semantic_audit_coverage,
    )

    materialized_jobs: list[UUID] = []

    async def fake_materialize_roadmap_rule_catalog(
        *,
        job_id: UUID,
        callback: object,
    ) -> None:
        _ = callback
        materialized_jobs.append(job_id)

    monkeypatch.setattr(
        "app.ai.agent._materialize_roadmap_rule_catalog",
        fake_materialize_roadmap_rule_catalog,
    )

    job_id = UUID("00000000-0000-0000-0000-000000000001")
    callback: Any = object()
    result, reviewed_chunks, total_chunks = await _run_review_until_coverage_complete(
        review_executor=executor,
        job_id=job_id,
        callback=callback,
    )

    assert reviewed_chunks == 3
    assert total_chunks == 88
    assert materialized_jobs == [job_id]
    assert len(executor.inputs) == 1
    assert result["output"] == "Pass 1: handoff 1"


@pytest.mark.asyncio
async def test_review_loop_reports_missing_plan_rules_without_retrying_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _RecordingReviewExecutor()

    async def fake_load_review_coverage(
        job_id: UUID,
        *,
        missing_limit: int | None = 25,
    ) -> tuple[int, int, list[dict[str, object]]]:
        _ = job_id, missing_limit
        return 12, 88, []

    monkeypatch.setattr(
        "app.ai.agent._load_review_coverage",
        fake_load_review_coverage,
    )

    roadmap_states = [(True, False), (True, True)]

    async def fake_roadmap_state(_job_id: UUID) -> tuple[bool, bool]:
        return roadmap_states.pop(0)

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_catalog_state",
        fake_roadmap_state,
    )

    async def fake_roadmap_review_state(_job_id: UUID) -> tuple[bool, list[str]]:
        return True, ["RC-W3-09"]

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_review_rule_state",
        fake_roadmap_review_state,
    )

    async def fake_semantic_audit_coverage(_job_id: UUID) -> list[str]:
        return []

    monkeypatch.setattr(
        "app.ai.agent._load_semantic_audit_plan_coverage",
        fake_semantic_audit_coverage,
    )

    async def fake_materialize_roadmap_rule_catalog(
        *,
        job_id: UUID,
        callback: object,
    ) -> None:
        _ = job_id, callback

    monkeypatch.setattr(
        "app.ai.agent._materialize_roadmap_rule_catalog",
        fake_materialize_roadmap_rule_catalog,
    )

    result, _reviewed_chunks, _total_chunks = await _run_review_until_coverage_complete(
        review_executor=executor,
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        callback=object(),
    )

    assert len(executor.inputs) == 1
    assert result["output"] == (
        "Pass 1: handoff 1\n\n"
        "Some roadmap review rules were not included in the unified category "
        "review plan within the bounded review budget: RC-W3-09. No issue was "
        "created without sufficient source evidence."
    )


@pytest.mark.asyncio
async def test_review_loop_reports_missing_semantic_category_items_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _RecordingReviewExecutor()

    async def fake_load_review_coverage(
        job_id: UUID,
        *,
        missing_limit: int | None = 25,
    ) -> tuple[int, int, list[dict[str, object]]]:
        _ = job_id, missing_limit
        return 12, 88, []

    monkeypatch.setattr(
        "app.ai.agent._load_review_coverage",
        fake_load_review_coverage,
    )

    async def fake_roadmap_state(_job_id: UUID) -> tuple[bool, bool]:
        return False, False

    monkeypatch.setattr(
        "app.ai.agent._load_roadmap_catalog_state",
        fake_roadmap_state,
    )

    missing_items = ["category_probe:performance.n_plus_one"]

    async def fake_semantic_audit_coverage(_job_id: UUID) -> list[str]:
        return missing_items

    monkeypatch.setattr(
        "app.ai.agent._load_semantic_audit_plan_coverage",
        fake_semantic_audit_coverage,
    )

    result, _reviewed_chunks, _total_chunks = await _run_review_until_coverage_complete(
        review_executor=executor,
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        callback=object(),
    )

    assert len(executor.inputs) == 1
    assert result["output"] == (
        "Pass 1: handoff 1\n\n"
        "Some unified category review items were not source-searched within "
        "the bounded review budget: category_probe:performance.n_plus_one. "
        "No issue was created without sufficient source evidence."
    )


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
