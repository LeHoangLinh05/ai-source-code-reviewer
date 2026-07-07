# P6.15-P6.25 AI Agent + Tools Summary

## Scope completed

- Added LangChain dependencies for the AI agent module.
- Added `backend/app/ai/llm_config.py` with Gemini primary (`gemini-2.0-flash`) and OpenAI fallback (`gpt-4o-mini`), both using API keys from `core/config.py`.
- Copied `REVIEW_SYSTEM_PROMPT` verbatim into `backend/app/ai/prompts.py`.
- Implemented the five LangChain tools:
  - `analyze_project_structure`
  - `read_file_chunk`
  - `search_coding_standard`
  - `generate_issue`
  - `generate_final_report`
- Added runtime context for tools so public tool schemas do not expose DB/session/sandbox internals.
- Added centralized MongoDB tool-call logging in `backend/app/ai/agent.py` through a single callback handler.
- Added ReAct agent executor with `max_iterations=20`.
- Integrated pipeline order:
  - clone
  - structure analysis
  - roadmap compliance
  - static analysis and secret scan
  - chunk
  - AI agent
  - final report
- Added chunk metadata persistence before AI review.
- Added backend enforcement for AI issue validation:
  - reject `confidence < 0.7`
  - reject `category="security"` with empty references
  - reject `category="requirement"`
  - reject invalid source line ranges by reading the real sandbox file
  - hardcode `source="ai_review"`
- Added dedup/static cross-check behavior:
  - duplicate roadmap-rule issues are skipped
  - duplicate AI issues are skipped
  - AI severity is not allowed to be lower than a matching static issue
- Added report generation that preserves roadmap `compliance_score` and `bonus_score` instead of recalculating them.

## Roadmap MongoDB contract observed

Code was checked against the actual `RoadmapComplianceChecker` and Mongo schemas.

Actual `roadmap_compliance_results` document fields:

- `job_id`
- `rule_profile`
- `checked_at`
- `results`
- `verification_queue`
- `compliance_score`
- `bonus_score`

Actual `results` item fields:

- `rule_id`
- `status`
- `severity`
- `week`
- `skill_group`

Actual `verification_queue` item fields:

- `rule_id`
- `file_path`
- `ai_hint`

## Spec mismatches flagged

- `ai_agent.md` example summary mentions priority/requirement text such as `Thiếu (P0): ...`, but persisted MongoDB `results` currently do not include `priority` or `requirement`. `analyze_project_structure` therefore summarizes roadmap status using only stored fields: pass/provisional counts, fail `rule_id`, fail `severity`, scores, and verification queue size.
- `ai_agent.md` mentions `app/analysis/chunker/`, while the actual working chunker is `backend/app/analyzers/code_chunker.py`. The implementation uses the real existing chunker and does not modify it.
- LangChain 1.x keeps the older `AgentExecutor`/`create_react_agent` API under `langchain_classic.agents`. The code imports from there so the implementation still uses the required ReAct executor API on the current Python 3.14 environment.

## Validation run

Passed:

- `python -m pytest backend\tests\unit`
- `python -m ruff check .`
- `python -m ruff format --check backend\app\ai backend\app\services\review_pipeline_service.py backend\app\workers\review_worker.py backend\tests\unit\test_ai_generate_issue_tool.py backend\tests\unit\test_ai_agent.py`
- `python -m mypy .`

Not fully passed:

- `python -m ruff format --check .` still reports pre-existing formatting changes in:
  - `backend/app/analyzers/code_chunker.py`
  - `backend/tests/unit/test_code_chunker.py`

Those files belong to the completed AST chunker area and were left untouched per instruction.

Not run:

- Live E2E agent runs with real MongoDB/PostgreSQL/LLM. Docker was not reachable in this session, DB ping timed out, and `GEMINI_API_KEY` is still the placeholder `change-me`.
