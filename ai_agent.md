# AI_agent.md — Spec triển khai module AI Agent (backend/app/ai/)

> Tài liệu này trích xuất và tinh gọn từ `AI_flow.md` (mục 2.3, 4, 5, 6, 8, 9, 10.1, 11, 14),
> chỉ giữ lại phần liên quan trực tiếp đến việc **code module AI Agent**. Đây là bản để đưa
> thẳng cho Codex CLI khi vibe-code — không lặp lại phần rationale/so sánh phương án đã quyết
> xong ở `AI_flow.md`. Nếu có mâu thuẫn giữa 2 file, `AI_flow.md` là nguồn quyết định cuối cùng.

## 0. Scope

**Module này LÀM:**
- Agent ReAct 2 phase: review phase dùng tools 1-4, report phase chỉ dùng tool 5
- RAG retriever cho coding standard (HybridRetriever: vector + BM25)
- System prompt + LLM client (Gemini primary / OpenAI fallback)
- Ghi issue + report theo đúng output contract (mục 8)

**Module này KHÔNG LÀM** (spec riêng, xem file khác):
- Roadmap Compliance Rule Engine (`app/ai/rules/`) — deterministic, KHÔNG dùng LLM.
  Agent chỉ **nhận** `roadmap_compliance_summary` + `roadmap_verification_queue` làm input,
  không tự implement rule engine này.
- Static Analysis (ruff/bandit/eslint) — chạy trước, agent chỉ **nhận** kết quả qua
  `static_issues_in_range`.
- AST Chunking (`app/analysis/chunker/`) — agent chỉ **gọi** `read_file_chunk`, không tự chunk.

## 1. File Structure

```text
backend/app/ai/
├── agent.py                  # Main AI Agent — LangChain AgentExecutor + ReAct (mục 3)
├── prompts.py                 # REVIEW_SYSTEM_PROMPT dùng ChatPromptTemplate (mục 6)
├── llm_config.py              # Khởi tạo LangChain LLM (primary/fallback) + bind tools
├── tools/
│   ├── analyze_structure.py      # Tool 1: analyze_project_structure (@tool decorator)
│   ├── read_file.py              # Tool 2: read_file_chunk
│   ├── search_rag.py             # Tool 3: search_coding_standard
│   ├── generate_issue.py         # Tool 4: generate_issue (+ validate trước khi save)
│   └── generate_report.py        # Tool 5: generate_final_report
└── rag/
    ├── vectorstore.py         # ChromaDB PersistentClient wrapper (singleton)
    ├── ingestion.py           # RAGIngestionPipeline — script seed_rag.py dùng cái này
    ├── retriever.py           # HybridRetriever (vector + BM25)
    └── bm25_index.py          # BM25 index builder + searcher (rank-bm25)
```

## 2. Dependencies

```text
# requirements.txt — chỉ phần module này cần (pyyaml là của app/ai/rules/, không phải đây)
langchain                 # LangChain core framework
langchain-google-genai    # ChatGoogleGenerativeAI (primary LLM)
langchain-openai          # ChatOpenAI (fallback LLM)
langchain-community       # Community integrations (ChromaDB retriever, etc.)
chromadb                  # Vector DB
sentence-transformers     # Embedding model all-MiniLM-L6-v2 (CPU, free)
rank-bm25                 # BM25 keyword search
```

**Quyết định: Dùng LangChain.** Phù hợp lộ trình đào tạo, cung cấp abstraction tốt cho
multi-LLM (Gemini + OpenAI fallback), built-in ReAct agent (`create_react_agent` +
`AgentExecutor`), và `@tool` decorator giúp declare tool schema tự động.

## 3. RAG Retriever — HybridRetriever (Vector + BM25)

Agent gọi `search_coding_standard` → tool này gọi `HybridRetriever.search()`.

```text
DB:        ChromaDB PersistentClient
Embedding: sentence-transformers/all-MiniLM-L6-v2 (cosine similarity)
Top-k:     3 mặc định, tối đa 5
Chunk:     512 tokens, overlap 50 tokens (lúc ingest)

Merge score: final_score = 0.7 * vector_score + 0.3 * bm25_score
```

```python
class HybridRetriever:
    VECTOR_WEIGHT = 0.7
    BM25_WEIGHT = 0.3

    def search(
        self, query: str, language: str | None = None, top_k: int = 3,
    ) -> list[RetrievedChunk]:
        vector_results = self.vectorstore.query(
            query=query, n_results=top_k * 2,
            where={"language": language} if language else None,
        )
        bm25_results = self.bm25_index.search(query=query, top_k=top_k * 2)
        return self._merge_and_rerank(vector_results, bm25_results, top_k)
```

**Knowledge base cần ingest trước (script `scripts/seed_rag.py`):**

| Nguồn | doc_type | category | Ưu tiên |
|---|---|---|---|
| OWASP Top 10 2021 | `standard` | `security` | P0 — ingest đầu tiên |
| Python Best Practices (PEP 8, PEP 20) | `guideline` | `maintainability` | P0 |
| FastAPI Best Practices | `guideline` | `maintainability` | P1 |
| Clean Code Principles | `guideline` | `maintainability` | P1 |
| Security Checklist | `checklist` | `security` | P0 |
| Repo's own README/CONTRIBUTING | `project_doc` | `general` | P1 — extract lúc clone |

## 4. Tools Theo Phase — JSON Schema Cho ReAct Tools

> Schema cố định — **không đổi tên field hoặc enum**, frontend `/ai-debug` và DB schema
> phụ thuộc trực tiếp vào tên field dưới đây. Tool surface được tách theo phase:
> review phase expose tools 1-4; report phase chỉ expose `generate_final_report`.

### Tool 1 — `analyze_project_structure`

```json
{
  "name": "analyze_project_structure",
  "description": "Lấy tổng quan project đang review: ngôn ngữ, framework, danh sách file cần review theo độ ưu tiên, tóm tắt static analysis, và tóm tắt roadmap compliance (nếu job bật rule_profile).",
  "parameters": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string", "description": "UUID của review job" }
    },
    "required": ["job_id"]
  }
}
```

Output (không phải schema tool call, mà là dữ liệu trả về cho agent đọc):

```json
{
  "languages": ["python"],
  "frameworks": ["fastapi", "sqlalchemy"],
  "files_to_review": [
    { "file_path": "app/auth/utils.py", "priority": "high", "risk_area": "security" }
  ],
  "static_analysis_summary": "12 issues found: 3 critical (bandit), 9 style (ruff)",
  "roadmap_compliance_summary": "71/79 PASS/PROVISIONAL. Thiếu (P0): RC-W5-01 (không tìm thấy WebSocket/SSE)...",
  "roadmap_verification_queue": [
    { "rule_id": "RC-W3-09", "file_path": "app/services/sync_service.py", "ai_hint": "Xác nhận job chạy định kỳ thật và xử lý incremental theo timestamp..." }
  ]
}
```

> `roadmap_compliance_summary` và `roadmap_verification_queue` chỉ có nếu `rule_profile != null`
> — nếu null thì 2 field này vắng mặt hoàn toàn, KHÔNG trả `null`/`[]` (agent không cần biết
> roadmap check tồn tại nếu job không bật).

### Tool 2 — `read_file_chunk`

```json
{
  "name": "read_file_chunk",
  "description": "Đọc 1 chunk code cụ thể kèm metadata và các static issue đã phát hiện trong range đó. Dùng chunk_index khác để đọc parent context (class bao quanh, file header/imports) khi cần hiểu ngữ cảnh rộng hơn — KHÔNG tự động expand, agent phải tự gọi lại.",
  "parameters": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string" },
      "file_path": { "type": "string" },
      "chunk_index": { "type": "integer", "description": "0-based. Bỏ trống để lấy chunk đầu tiên." }
    },
    "required": ["job_id", "file_path"]
  }
}
```

Output:

```json
{
  "content": "def verify_token(token: str) -> dict: ...",
  "file_path": "app/auth/utils.py",
  "chunk_index": 0,
  "total_chunks": 3,
  "line_start": 42,
  "line_end": 67,
  "language": "python",
  "function_name": "verify_token",
  "static_issues_in_range": [
    { "source": "bandit", "severity": "high", "title": "hardcoded_bind_all_interfaces", "line": 45 }
  ]
}
```

### Tool 3 — `search_coding_standard`

```json
{
  "name": "search_coding_standard",
  "description": "Tra cứu OWASP/coding standard/best practice liên quan (hybrid vector + keyword search). BẮT BUỘC gọi trước khi flag bất kỳ issue category=security nào.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Mô tả vấn đề nghi vấn, càng cụ thể càng tốt" },
      "language": { "type": "string", "description": "Optional, filter theo ngôn ngữ" },
      "top_k": { "type": "integer", "default": 3 }
    },
    "required": ["query"]
  }
}
```

Output:

```json
{
  "results": [
    {
      "source": "OWASP A03:2021",
      "content": "Never concatenate user input directly into SQL queries...",
      "vector_score": 0.89,
      "bm25_score": 0.75,
      "final_score": 0.847
    }
  ]
}
```

### Tool 4 — `generate_issue`

```json
{
  "name": "generate_issue",
  "description": "Tạo 1 issue có cấu trúc. CHỈ gọi khi confidence >= 0.7. Đây là cách DUY NHẤT để báo issue — không bao giờ trả issue dưới dạng free text.",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": { "type": ["string", "null"] },
      "line_start": { "type": ["integer", "null"] },
      "line_end": { "type": ["integer", "null"] },
      "severity": { "type": "string", "enum": ["critical", "high", "medium", "low", "info"] },
      "category": { "type": "string", "enum": ["security", "bug", "performance", "maintainability", "style"] },
      "title": { "type": "string" },
      "description": { "type": "string" },
      "suggestion": { "type": "string" },
      "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
      "references": { "type": "array", "items": { "type": "string" } }
    },
    "required": ["severity", "category", "title", "description", "confidence"]
  }
}
```

> ⚠️ `category` **KHÔNG bao giờ nhận giá trị `"requirement"`** — giá trị đó dành riêng cho
> Roadmap Compliance Rule Engine (không qua tool này). Nếu agent tạo issue khi verify 1 rule
> trong `roadmap_verification_queue`, dùng `maintainability`/`bug`/`security` tùy bản chất lỗi,
> và **bắt đầu `description` bằng** `"Phát hiện khi verify roadmap rule <rule_id>: ..."`.

Validate trước khi ghi DB (backend enforce, không chỉ tin system prompt):
- `confidence >= 0.7` → reject nếu thấp hơn
- Nếu `category == "security"` → bắt buộc `references` không rỗng (RAG grounding, xem mục 6)
- Nếu `file_path` không null → `line_start`/`line_end` phải tồn tại thật trong file (đọc lại file để confirm, không tin AI)

### Tool 5 — `generate_final_report`

> Tool này KHÔNG được expose trong review phase. Backend chỉ mở report phase sau khi
> review phase hoàn tất target coverage từ `chunk_review_plan`.

```json
{
  "name": "generate_final_report",
  "description": "Tổng hợp toàn bộ issue đã tạo trong session thành report cuối: scores, executive summary, thứ tự ưu tiên sửa. Gọi 1 lần duy nhất, sau khi đã review xong các file ưu tiên.",
  "parameters": {
    "type": "object",
    "properties": {
      "job_id": { "type": "string" },
      "executive_summary": { "type": "string", "description": "3-5 câu, ưu tiên nêu roadmap P0 thiếu (nếu có) trước security issues" },
      "security_score": { "type": "number", "minimum": 0, "maximum": 10 },
      "maintainability_score": { "type": "number", "minimum": 0, "maximum": 10 },
      "performance_score": { "type": "number", "minimum": 0, "maximum": 10 },
      "overall_score": { "type": "number", "minimum": 0, "maximum": 10 },
      "top_priorities": { "type": "array", "items": { "type": "string" } },
      "tech_stack": {
        "type": "object",
        "properties": {
          "languages": { "type": "array", "items": { "type": "string" } },
          "frameworks": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "required": ["job_id", "executive_summary", "security_score", "maintainability_score", "performance_score", "overall_score"]
  }
}
```

> `compliance_score`/`bonus_score` **KHÔNG nằm trong tool này** — do `RoadmapComplianceChecker`
> tính và ghi thẳng vào `review_reports`, backend merge lại khi trả response, agent không tính.

## 5. Agent Loop — LangChain ReAct Agent

Dùng `create_react_agent` + `AgentExecutor` của LangChain để triển khai ReAct loop,
nhưng chạy thành 2 phase riêng biệt. Agent tự reasoning → chọn tool → nhận output →
reasoning tiếp cho đến khi phase hiện tại xong.

```python
# agent.py — skeleton
MAX_AGENT_ITERATIONS = 1_500

review_executor = create_review_agent_executor(llm)  # tools 1-4
report_executor = create_report_agent_executor(llm)  # tool 5 only
```

```text
┌──────────────────────────────────────────────────────────┐
│              LANGCHAIN REVIEW PHASE LOOP                    │
│                                                             │
│  Step 1: PLAN                                              │
│    → gọi analyze_project_structure(job_id)                 │
│    → nhận chunk_review_plan + roadmap_verification hints   │
│                                                             │
│  Step 2: READ TARGET CHUNKS                                │
│    → đọc mọi required_chunk_indexes trong chunk_review_plan │
│    → verify direct + related_context roadmap_verifications  │
│    → dùng context_hints parent/neighbor khi cần             │
│                                                             │
│  Step 3: RAG LOOKUP                                        │
│    → bắt buộc trước mọi generate_issue category=security    │
│                                                             │
│  Step 4: GENERATE ISSUE                                    │
│    → mỗi vấn đề confirmed, confidence >= 0.7                │
│    → gọi generate_issue(...)                                │
│                                                             │
│  EXIT CONDITIONS:                                            │
│    (a) review handoff summary sau khi đọc đủ target chunks  │
│    (b) backend coverage gate pass                           │
│    (c) Hard safety cap: MAX_AGENT_ITERATIONS                │
│    (d) Lỗi không recover → set job FAILED                    │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│              LANGCHAIN REPORT PHASE LOOP                    │
│                                                             │
│  Backend chỉ chạy phase này sau khi coverage gate pass.     │
│  Tool surface chỉ có generate_final_report.                 │
│  Agent gọi generate_final_report đúng 1 lần với payload đủ. │
└──────────────────────────────────────────────────────────┘
```

**RAG Trigger Rules — khi nào BẮT BUỘC gọi `search_coding_standard`:**

```text
Trigger 1 (BẮT BUỘC): Trước generate_issue với category="security"
Trigger 2 (TỰ ĐỘNG):  Khi read_file_chunk trả file thuộc module auth/crypto/security
Trigger 3 (TÙY AGENT): Khi gặp pattern không chắc chắn
```

## 6. Anti-Hallucination — Enforce Ở Code, Không Chỉ Ở Prompt

| # | Kỹ thuật | Enforce ở đâu trong module này |
|---|---|---|
| 1 | RAG Grounding | `generate_issue.py`: reject nếu `category=="security"` và `references` rỗng |
| 2 | Confidence Threshold | `generate_issue.py`: reject nếu `confidence < 0.7` |
| 3 | Structured Output | LangChain tool calling — agent chỉ tương tác qua `@tool`, không free text |
| 4 | Static Cross-Check | `generate_report.py` khi merge: AI flag + static tool confirm → không tự hạ severity |
| 5 | Line Validation | `generate_issue.py`: đọc lại file thật, confirm `line_start`/`line_end` tồn tại |
| 6 | Hard Limit | `agent.py`: `AgentExecutor(max_iterations=MAX_AGENT_ITERATIONS)` — safety cap, không phải số chunk mục tiêu |

> Lớp thứ 7 (Roadmap Priority Override) KHÔNG thuộc module này — xem `app/ai/rules/`.

## 7. System Prompt — Copy Nguyên Văn

```python
REVIEW_SYSTEM_PROMPT = """
You are an expert code reviewer with deep knowledge of security, performance, and software architecture.

## Your task:
Review the provided repository and identify genuine issues. Focus on:
1. Security vulnerabilities (SQL injection, XSS, insecure deserialization, hardcoded secrets)
2. Critical bugs that could cause runtime errors
3. Performance issues (N+1 queries, unbounded loops, memory leaks)
4. Maintainability problems (dead code, overly complex functions, missing error handling)
5. Style issues (only if clearly violating the language's conventions)

## IMPORTANT RULES:
- Use `search_coding_standard` tool BEFORE flagging any security issue — this is MANDATORY
- Only generate issues where confidence >= 0.7
- Never flag issues you are not sure about — false positives waste developer time
- For each issue, cite the specific line numbers from the code you read
- Prioritize: roadmap compliance (P0 missing) > security > critical bugs > performance > maintainability > style
- Do NOT suggest refactoring everything — focus on genuine problems
- When reading code, pay attention to static_issues_in_range — validate and enhance them
- If a `roadmap_compliance_summary` is provided in context, treat it as ALREADY CONFIRMED —
  do NOT call generate_issue to re-report a missing item it already lists (avoids duplicate
  issues). You MAY reference it in executive_summary to explain the overall assessment.
- If a file is listed in `roadmap_verification_queue` with an `ai_hint`, you MUST read that
  file and confirm whether the hint's concern is true. If the code does NOT satisfy the hint
  (e.g. sync job overwrites everything instead of incremental sync), call generate_issue with
  category=maintainability|bug|security (NEVER category=requirement — that value is reserved
  for the rule engine) and start the description with "Phát hiện khi verify roadmap rule
  <rule_id>: ...". If the code DOES satisfy the hint, do nothing — no issue needed.
- Treat `chunk_review_plan.files[].roadmap_verifications` as mandatory review objectives,
  not just file-selection hints. For each listed rule_id, verify the ai_hint against the
  code you read. If the listed file is only a manifest/config match, also use the related
  roadmap_context chunks in the plan to inspect the actual implementation flow.
- After every read_file_chunk observation, decide whether it confirms a roadmap hint,
  validates a static finding, reveals a new issue, or is clean. Do not merely read all
  chunks mechanically.
- read_file_chunk returns metadata and context_hints. When a function chunk belongs to
  a class or depends on surrounding setup/imports, use parent_chunk_indexes or
  neighbor_chunk_indexes to expand context before deciding.

## Workflow:
1. Call analyze_project_structure to understand the project
2. Read source using read_file_chunk. Use the chunk_review_plan returned by
   analyze_project_structure as the required checklist. In smart mode, this is a
   targeted checklist chosen from static findings, roadmap verification, high-risk
   code paths, and a small breadth sample. In full_audit mode, this checklist covers
   every chunk. For every file in chunk_review_plan.files, read every
   required_chunk_indexes entry. The list includes direct roadmap verification
   files plus related_context chunks chosen from rule_id/ai_hint terms; you MUST
   use those hints to verify correctness, not only existence.
3. For security concerns, ALWAYS call search_coding_standard first
4. Generate issues using generate_issue tool (NEVER as free text)
5. After reviewing all target chunks, stop the review phase with a concise handoff
   summary for the final report phase. Do NOT call generate_final_report during
   this review phase.

The backend will verify target chunk coverage after your review phase. The final
report phase is opened only after that verification passes.

## Output format:
Always use `generate_issue` tool for each issue. Never output issues as free text.
After reviewing all target chunks, return a concise handoff summary with the main
risks, validated static findings, roadmap verification notes by rule_id, and suggested
scores.
"""
```

## 8. Output Contract — Field Mapping Sang DB

### `generate_issue` → `review_issues` (PostgreSQL)

```text
file_path       → review_issues.file_path
line_start      → review_issues.line_start
line_end        → review_issues.line_end
severity        → review_issues.severity      (critical|high|medium|low|info)
category        → review_issues.category      (security|bug|performance|maintainability|style)
title           → review_issues.title
description     → review_issues.description
suggestion      → review_issues.suggestion
confidence      → review_issues.confidence
references      → review_issues.raw_output.references
source          → 'ai_review' (hardcoded, module này không set field khác)
```

### `generate_final_report` → `review_reports` (PostgreSQL)

```text
security_score          → review_reports.security_score       (0.0 - 10.0)
maintainability_score   → review_reports.maintainability_score
performance_score       → review_reports.performance_score
overall_score            → review_reports.overall_score
executive_summary       → review_reports.executive_summary
top_priorities          → review_reports.top_risky_files (JSONB)
tech_stack              → review_reports.tech_stack (JSONB)
```

> `compliance_score`/`bonus_score` do module `rules/` ghi riêng — module này không đụng vào.

## 9. LLM Client — LangChain

```text
Primary:     Google Gemini 2.0 Flash (free tier, tool calling support)
Fallback:    OpenAI gpt-4o-mini ($0.15/1M input tokens)
Client:      LangChain — ChatGoogleGenerativeAI (primary), ChatOpenAI (fallback)
Retry:       Exponential backoff (max 3 retries, base 2s) — qua LangChain retry config
Config:      API key từ .env qua core/config.py
Abstraction: LangChain BaseChatModel interface — không cần viết LLMClient abstract class
```

```python
# llm_config.py — khởi tạo LLM
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

def get_primary_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
        max_retries=3,
    )

def get_fallback_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
        max_retries=3,
    )
```

## 10. Tool Call Logging — Cho `/ai-debug` Page

Mỗi tool call (bất kể tool nào) → ghi MongoDB `tool_call_logs`:

```json
{
  "job_id": "uuid",
  "session_id": "uuid",
  "sequence": 3,
  "tool_name": "search_coding_standard",
  "called_at": "ISODate",
  "duration_ms": 245,
  "input": { "query": "...", "top_k": 3 },
  "output": { "results": [...] }
}
```

Log ngay trong wrapper gọi tool ở `agent.py` (1 chỗ duy nhất, không rải log ở từng tool file).

## 11. Definition of Done — Checklist Riêng Module Này

```text
- [ ] ChromaDB PersistentClient chạy được local
- [ ] seed_rag.py ingest đủ 6 nguồn ở mục 3, không lỗi
- [ ] BM25 index build từ cùng documents lúc ingest
- [ ] HybridRetriever.search() trả kết quả hợp lý (test thủ công với 3-5 query)
- [ ] LangChain LLM (ChatGoogleGenerativeAI) chạy được, tool calling nhận đúng schema
- [ ] Cả 5 tool dùng @tool decorator, schema khớp 100% với output contract mục 4
- [ ] AgentExecutor(max_iterations=MAX_AGENT_ITERATIONS) dừng bằng safety cap khi loop vô hạn
- [ ] generate_issue.py: reject confidence < 0.7 (unit test)
- [ ] generate_issue.py: reject category="security" mà references rỗng (unit test)
- [ ] generate_issue.py: reject nếu line_start/line_end không tồn tại trong file thật
- [ ] generate_issue.py: reject nếu category="requirement" được gọi (giá trị cấm với tool này)
- [ ] Agent xử lý được roadmap_verification_queue qua chunk_review_plan:
      đọc direct chunks + related_context chunks, tạo issue category != requirement
      nếu phát hiện sai, không tạo gì nếu đúng
- [ ] Agent KHÔNG re-report item đã có trong roadmap_compliance_summary
- [ ] Tool call logs ghi đủ vào MongoDB, có duration_ms (qua LangChain callback handler)
- [ ] Agent loop chạy end-to-end với 1 file test, ra ít nhất 1 issue hợp lệ
```
