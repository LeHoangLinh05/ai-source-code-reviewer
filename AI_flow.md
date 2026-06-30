# AI_flow.md — RepoGuard AI Pipeline: Quyết Định Cuối Cùng

> **Phương pháp chính thức:**
> **Evidence-Grounded Agentic Hybrid RAG for Repository-Level Code Review**
>
> Tài liệu này là **quyết định cuối cùng** — không phải gợi ý hay thảo luận.
> Code theo đúng flow này, không thay đổi trừ khi có lý do kỹ thuật bắt buộc.

---

## 1. TỔNG QUAN PHƯƠNG PHÁP

### 1.1 Tại sao KHÔNG dùng Naive RAG

Naive RAG (`Code → chunk → embedding → search top-k → LLM → output`) không phù hợp vì:

- Source code cần **line number chính xác**, không phải "khoảng chừng"
- Một lỗi có thể **nằm rải ở nhiều file** (auth flow = router + service + middleware + model)
- Vector search dễ lấy đoạn **"na ná đúng"** thay vì đúng thật
- Code review cần **bằng chứng cụ thể**, không phải "AI cảm thấy có lỗi"
- Metadata (function_name, imports, module, risk_area) quan trọng hơn semantic similarity đơn thuần

### 1.2 Phương pháp được chọn

```text
Evidence-Grounded Agentic Hybrid RAG
├── Code Retrieval:      AST Chunking + Metadata Filtering + Parent-Child Context
├── Knowledge Retrieval: Hybrid RAG (Vector Search + Keyword Matching)
├── Agent Pattern:       ReAct Tool Calling (tự quyết tool → tự truyền input → tự dùng output)
├── Grounding:           Static Analysis Cross-Check + Evidence Mapping
└── Output:              Structured JSON (generate_issue tool, không free text)
```

### 1.3 Vai trò đúng của từng thành phần

| Thành phần | Vai trò | KHÔNG PHẢI là |
|---|---|---|
| Static Analysis (ruff, bandit, eslint) | Phát hiện lỗi **rõ ràng**, deterministic | "Bước phụ cho AI" |
| AI Agent | Phát hiện lỗi **logic/ngữ cảnh**, cross-file reasoning | "Chatbot hỏi đáp" |
| RAG Knowledge Base | Cung cấp **chuẩn tham chiếu** cho AI khi đánh giá | "Trung tâm của hệ thống" |
| Metadata Filtering | Giúp tìm **đúng code** thay vì search toàn repo | "Feature phụ" |
| Evidence Mapping | Bảo đảm mỗi issue có **bằng chứng cụ thể** | "Nice-to-have" |

> ⚠️ **Nguyên tắc cốt lõi:** Trung tâm của RepoGuard là **Review Pipeline**, không phải RAG. RAG chỉ là một phần giúp AI có căn cứ khi đánh giá code.

---

## 2. HAI LỚP RETRIEVAL — QUYẾT ĐỊNH KIẾN TRÚC

### 2.1 Tách riêng 2 loại retrieval

```text
Lớp 1: Repository Context Retrieval
  → Tìm file/chunk code liên quan trong repository đang review
  → Dùng: AST chunking + metadata filtering + parent-child context

Lớp 2: Coding Standard Retrieval (Knowledge Base)
  → Tìm coding standards, OWASP, best practices
  → Dùng: Hybrid RAG (vector search + keyword matching)
```

**Tại sao tách?** Vì source code và tài liệu chuẩn có bản chất khác nhau:
- Code cần metadata chính xác (file_path, line_start, function_name)
- Tài liệu chuẩn cần semantic search + keyword matching

### 2.2 Lớp 1 — Repository Context Retrieval

#### AST-based Code Chunking (đã có trong plan, giữ nguyên)

```text
File code
→ Parse AST (Python: ast module, JS: acorn/tree-sitter)
→ Tách theo class/function boundary
→ Fallback: fixed 60 lines, overlap 10 lines
→ MAX_TOKENS_PER_CHUNK = 1500
```

#### Metadata bắt buộc cho mỗi chunk

```json
{
  "job_id": "uuid",
  "file_path": "app/auth/utils.py",
  "language": "python",
  "chunk_type": "function",
  "chunk_index": 0,
  "total_chunks": 3,
  "function_name": "verify_token",
  "class_name": null,
  "line_start": 42,
  "line_end": 67,
  "imports": ["jwt", "datetime"],
  "module": "auth",
  "risk_area": "security",
  "has_static_issues": true,
  "token_count": 380
}
```

**Quyết định về `risk_area`:** Tự động gán dựa trên heuristic:

```text
risk_area = "security"    → file trong thư mục auth/, security/, crypto/, middleware/
                          → hoặc import jwt, bcrypt, hashlib, cryptography, secrets
risk_area = "database"    → file trong thư mục db/, models/, repositories/
                          → hoặc import sqlalchemy, pymongo
risk_area = "api"         → file trong thư mục routers/, api/, endpoints/
risk_area = "config"      → file tên config.py, settings.py, .env
risk_area = "general"     → mặc định
```

#### Metadata Filtering khi AI cần đọc code

Khi AI review auth module, **KHÔNG search toàn repo**. Filter trước:

```text
module = "auth" AND language = "python"
→ rồi mới lấy chunks
→ ưu tiên chunk có risk_area = "security"
```

Đây là logic trong `read_file_chunk` tool — khi agent yêu cầu đọc file, tool tự lấy metadata kèm theo (bao gồm `static_issues_in_range`).

#### Parent-Child Context Expansion

```text
Khi AI đang đọc 1 function chunk:
  → Nếu cần hiểu context rộng hơn
  → Agent gọi read_file_chunk với chunk_index khác (parent class, file header/imports)
  → Tool trả về chunk liền kề + metadata parent

Quyết định: KHÔNG tự động inject parent context.
  → Agent tự quyết định khi nào cần đọc thêm qua tool calling.
  → Lý do: giữ token budget gọn, agent thông minh hơn auto-expand.
```

### 2.3 Lớp 2 — Coding Standard Retrieval (Knowledge Base RAG)

#### Vector Search (Primary) — ChromaDB

```text
DB:        ChromaDB PersistentClient
Embedding: sentence-transformers/all-MiniLM-L6-v2 (chạy CPU, free)
Space:     cosine similarity
Top-k:     3 (default), tối đa 5
Chunk:     512 tokens, overlap 50 tokens
```

Giữ nguyên quyết định ChromaDB từ plan — đủ cho MVP, chạy local, Python native.

#### Keyword Matching (Secondary) — BM25 in-memory

```text
Thêm BM25 search song song với vector search.
Library: rank-bm25 (pip install rank-bm25, lightweight)

Flow:
1. Vector search → top-k results (semantic)
2. BM25 search → top-k results (keyword exact match)
3. Merge + deduplicate → re-rank by combined score
4. Return final top-k
```

**Tại sao cần BM25?** OWASP/security docs có keyword chính xác:
- "SQL injection", "XSS", "CSRF", "JWT", "hardcoded secret"
- Vector search hiểu nghĩa tốt, nhưng keyword search bắt thuật ngữ chính xác hơn

**Quyết định triển khai:**
- BM25 index build lúc ingestion (cùng lúc với ChromaDB insert)
- Lưu in-memory (knowledge base nhỏ, không cần persistent BM25)
- Merge score: `final_score = 0.7 * vector_score + 0.3 * bm25_score` (tunable)
- File: `backend/app/ai/rag/retriever.py` — class `HybridRetriever`

#### Metadata Filtering cho Knowledge Base

Mỗi document chunk có metadata:

```json
{
  "source": "OWASP Top 10 - A03:2021",
  "chunk_index": 2,
  "language": "python",
  "doc_type": "standard",
  "category": "security",
  "ingested_at": "2026-06-30T00:00:00Z"
}
```

Khi search, filter theo `language` và `doc_type` nếu có context.

#### Knowledge Base Sources — Cố định

| Nguồn | doc_type | category | Ưu tiên |
|---|---|---|---|
| OWASP Top 10 2021 | `standard` | `security` | P0 — ingest đầu tiên |
| Python Best Practices (PEP 8, PEP 20) | `guideline` | `maintainability` | P0 |
| FastAPI Best Practices | `guideline` | `maintainability` | P1 |
| Clean Code Principles | `guideline` | `maintainability` | P1 |
| Security Checklist (hardcoded secrets, auth patterns) | `checklist` | `security` | P0 |
| Repo's own README/CONTRIBUTING | `project_doc` | `general` | P1 — extract lúc clone |

---

## 3. AI AGENT — REACT TOOL CALLING

### 3.1 Agent Pattern: ReAct (Reasoning + Acting)

```text
Agent KHÔNG phải:
  - Gọi LLM 1 lần, lấy text, parse
  - Pipeline cứng: step1 → step2 → step3

Agent LÀ:
  - LLM nhìn danh sách tools + context
  - LLM tự quyết định gọi tool nào, truyền input gì
  - Nhận output → reasoning → quyết định bước tiếp
  - Loop cho đến khi gọi generate_final_report hoặc đạt limit
```

### 3.2 Năm Tools Cố Định

| # | Tool Name | Mục đích | Input chính | Output chính |
|---|---|---|---|---|
| 1 | `analyze_project_structure` | Lấy tổng quan project, danh sách file cần review | `job_id` | languages, frameworks, `files_to_review[]`, static_analysis_summary |
| 2 | `read_file_chunk` | Đọc code chunk cụ thể | `job_id, file_path, chunk_index` | content, line_start/end, `static_issues_in_range[]` |
| 3 | `search_coding_standard` | RAG lookup: tìm standard/guideline liên quan | `query, language?, top_k=3` | `results[]` (source, content, similarity_score) |
| 4 | `generate_issue` | Tạo structured issue | file_path, severity, category, title, description, confidence | Issue object (validate trước khi save) |
| 5 | `generate_final_report` | Tổng hợp report + scores | `job_id, all_issues[]` | scores, executive_summary, top_priorities |

**Contract cố định** — không đổi tên field hoặc enum vì frontend `/ai-debug` và DB schema phụ thuộc.

### 3.3 Agent Loop Flow

```text
┌──────────────────────────────────────────────────────────┐
│                    AGENT REACT LOOP                      │
│                                                          │
│  Step 1: PLAN                                            │
│    → gọi analyze_project_structure(job_id)               │
│    → nhận: file list, priorities, static summary         │
│    → reasoning: chọn file nào review trước               │
│                                                          │
│  Step 2: READ (loop qua files_to_review, priority=high)  │
│    → gọi read_file_chunk(job_id, file_path, chunk_index) │
│    → nhận: code + metadata + static_issues_in_range      │
│    → reasoning: phân tích code, tìm vấn đề              │
│                                                          │
│  Step 3: RAG LOOKUP (khi phát hiện nghi vấn)             │
│    → gọi search_coding_standard(query, language)         │
│    → nhận: OWASP/guideline chunks + similarity score     │
│    → reasoning: validate nghi vấn dựa trên standard      │
│                                                          │
│  Step 4: GENERATE ISSUE (cho mỗi vấn đề confirmed)      │
│    → gọi generate_issue(structured fields)               │
│    → chỉ khi confidence >= 0.7                           │
│                                                          │
│  Step 5: REPORT (sau khi review xong)                    │
│    → gọi generate_final_report(job_id, all_issues)       │
│    → tính scores, viết executive summary                 │
│                                                          │
│  EXIT CONDITIONS:                                        │
│    (a) generate_final_report đã được gọi                 │
│    (b) Hard limit 20 tool calls/session                  │
│    (c) Lỗi không recover → set job FAILED                │
└──────────────────────────────────────────────────────────┘
```

### 3.4 RAG Trigger Rules — Khi nào agent PHẢI gọi RAG

```text
Trigger 1 (BẮT BUỘC):
  Trước khi generate_issue với category = "security"
  → PHẢI gọi search_coding_standard(query=issue_description, language=lang)
  → Dùng retrieved context làm căn cứ trong issue.references

Trigger 2 (TỰ ĐỘNG):
  Khi read_file_chunk trả về file thuộc module auth/crypto/security
  → Agent nên retrieve OWASP guidelines tương ứng

Trigger 3 (TÙY AGENT):
  Khi gặp pattern không chắc chắn
  → Agent tự quyết định search thêm standard
```

### 3.5 LLM Client

```text
Primary:     Google Gemini 2.0 Flash (free tier, tool calling support)
Fallback:    OpenAI gpt-4o-mini ($0.15/1M input tokens)
Client:      google-genai SDK (Gemini) / openai SDK (OpenAI)
             → KHÔNG dùng LangChain trừ khi thật cần thiết
             → Direct SDK đơn giản hơn, debug dễ hơn, ít dependency hơn
Retry:       Exponential backoff (max 3 retries, base 2s)
Config:      API key từ .env qua core/config.py
Abstraction: LLMClient base class → GeminiClient, OpenAIClient
```

**Quyết định: Dùng Direct SDK, không dùng LangChain.**
Lý do:
- LangChain thêm abstraction layer không cần thiết cho 5 tools đơn giản
- Debug tool calling qua LangChain khó hơn direct SDK
- Giảm dependency size
- Gemini SDK (`google-genai`) và OpenAI SDK đều support tool calling native

---

## 4. STATIC ANALYSIS GROUNDING

### 4.1 Flow kết hợp Static + AI

```text
Phase 1: Static Analysis (deterministic, confidence = 1.0)
  → Ruff check → parse JSON → NormalizedIssue list
  → Bandit scan → parse JSON → NormalizedIssue list
  → ESLint check → parse JSON → NormalizedIssue list (nếu có JS/TS)
  → Secret scanner (regex) → findings list
  → Tất cả lưu MongoDB raw_static_analysis_outputs

Phase 2: Inject vào AI context
  → Trước khi gọi agent, inject:
    "Static analysis đã phát hiện các issues sau: [...]"
  → Agent biết đã có issue gì, không cần phát hiện lại

Phase 3: AI Review (qua tool calling)
  → AI validate static issues (giữ/bỏ/nâng severity nếu cần)
  → AI tìm thêm issues mà static tool KHÔNG detect được:
    • Logic bugs cross-file
    • Architecture anti-patterns
    • Missing error handling
    • N+1 queries
    • Insecure business logic

Phase 4: Merge + Deduplicate
  → Merge static issues + AI issues
  → Dedup theo (file_path, line_start, category)
  → Giữ source field rõ: 'ruff' | 'bandit' | 'eslint' | 'ai_review'
  → Cross-validation: nếu AI flag nhưng static tool không có → severity giữ nguyên
    nhưng ghi note trong issue
```

### 4.2 Static Tools — Cố định

| Tool | Ngôn ngữ | Command | Mục đích |
|---|---|---|---|
| `ruff` | Python | `ruff check --output-format=json` | Linting, style, basic bugs |
| `bandit` | Python | `bandit -r . -f json` | Security scan |
| `eslint` | JS/TS | `eslint --format json` | Linting + security rules |

**KHÔNG dùng:** pylint (chậm), semgrep (nặng setup — để optional advanced).

---

## 5. ANTI-HALLUCINATION — 6 LỚP BẢO VỆ

Đây là phần mentor hỏi kỹ nhất. Không thỏa hiệp.

| # | Kỹ thuật | Cách làm | Enforce ở đâu |
|---|---|---|---|
| 1 | **RAG Grounding** | Security issue PHẢI có RAG reference | System prompt + validate trong generate_issue |
| 2 | **Confidence Threshold** | Chỉ lưu issue có `confidence >= 0.7` | Validate trong `generate_issue.py` trước khi ghi DB |
| 3 | **Structured Output** | AI PHẢI dùng `generate_issue` tool, không free text | Tool calling enforcement, reject non-tool response |
| 4 | **Static Cross-Check** | AI flag + static tool confirm → tăng confidence | Logic trong report_service.py khi merge |
| 5 | **Line Validation** | line_start/line_end PHẢI tồn tại trong file thật | Backend validate trước insert review_issues |
| 6 | **Hard Limit** | Tối đa 20 tool calls/session | Enforce trong agent loop code, KHÔNG phải prompt |

---

## 6. REVIEW PIPELINE END-TO-END — 12 BƯỚC

```text
 ┌─────────────────────────────────────────────────────────────┐
 │                 REPOGUARD REVIEW PIPELINE                  │
 │                                                             │
 │  1. CLONE REPO                                              │
 │     git clone --depth 1 → /sandbox/{job_id}/                │
 │     validate: size <= 100MB, files <= 500                   │
 │                                                             │
 │  2. DETECT STRUCTURE                                        │
 │     language detect, framework detect, file tree builder     │
 │     → lưu MongoDB file_analysis_results                     │
 │                                                             │
 │  3. RUN STATIC ANALYSIS                                     │
 │     ruff + bandit (Python), eslint (JS/TS)                  │
 │     → parse → NormalizedIssue → lưu MongoDB                 │
 │                                                             │
 │  4. CHUNK CODE (AST-based)                                  │
 │     parse AST → class/function boundary                     │
 │     → gắn metadata (file_path, language, module, risk_area, │
 │       line_start, line_end, imports, function_name)          │
 │     → lưu MongoDB chunk_metadata                            │
 │                                                             │
 │  5. CHỌN FILES ƯU TIÊN                                      │
 │     heuristic: auth > api > db > services > utils > tests   │
 │     top 30 files, skip: generated, migrations, tests, lock  │
 │                                                             │
 │  6. INJECT CONTEXT CHO AGENT                                │
 │     project_context + static_analysis_results + file_list   │
 │                                                             │
 │  7. AGENT LẬP KẾ HOẠCH                                      │
 │     → gọi analyze_project_structure                         │
 │     → reasoning: chọn review order                          │
 │                                                             │
 │  8. AGENT REVIEW LOOP (cho mỗi file/chunk)                  │
 │     → read_file_chunk (đọc code + metadata + static issues) │
 │     → search_coding_standard (nếu nghi security issue)      │
 │     → generate_issue (confidence >= 0.7)                    │
 │     → expand parent context nếu cần (read thêm chunks)      │
 │                                                             │
 │  9. VALIDATE ISSUES                                         │
 │     → line_start/line_end có tồn tại trong file?            │
 │     → confidence >= 0.7?                                    │
 │     → security issue có RAG reference?                      │
 │     → drop/flag nếu không pass                              │
 │                                                             │
 │  10. DEDUPLICATE                                            │
 │     merge static issues + AI issues                         │
 │     dedup theo (file_path, line_start, category)            │
 │     giữ rõ source: 'ruff' | 'bandit' | 'ai_review'         │
 │                                                             │
 │  11. GENERATE REPORT + SCORES                               │
 │     → gọi generate_final_report                             │
 │     → tính security_score, maintainability_score,           │
 │       performance_score, overall_score                      │
 │     → viết executive_summary                                │
 │     → lưu PostgreSQL review_reports + review_issues         │
 │                                                             │
 │  12. LƯU EVIDENCE + CLEANUP                                │
 │     → tool_call_logs → MongoDB (cho /ai-debug page)         │
 │     → cleanup sandbox (rm /sandbox/{job_id}/)               │
 │     → publish COMPLETED event → Redis → SSE → Frontend      │
 └─────────────────────────────────────────────────────────────┘
```

---

## 7. RAG DESIGN — CẤU TRÚC MODULE

### 7.1 File Structure

```text
backend/app/ai/rag/
├── vectorstore.py      # ChromaDB client wrapper (singleton)
├── ingestion.py        # RAGIngestionPipeline
├── retriever.py        # HybridRetriever (vector + BM25)
└── bm25_index.py       # BM25 index builder + searcher
```

### 7.2 RAG Method Selection (ghi trong báo cáo/SRS)

```text
Method: Evidence-Grounded Agentic Hybrid RAG
Reason:
  1. Source code cần truy xuất theo metadata, AST, line number
  2. Security review cần grounding bằng OWASP/best practices
  3. Agent cần gọi tool linh hoạt theo từng loại issue
  4. Static analysis dùng để kiểm chứng và giảm hallucination
  5. Keyword matching (BM25) bắt thuật ngữ security chính xác
     mà vector search có thể miss
```

### 7.3 HybridRetriever — Pseudocode

```python
class HybridRetriever:
    """
    Kết hợp Vector Search (ChromaDB) + Keyword Search (BM25).
    Vector search hiểu semantic, BM25 bắt keyword chính xác.
    """

    VECTOR_WEIGHT = 0.7
    BM25_WEIGHT = 0.3

    def search(
        self,
        query: str,
        language: str | None = None,
        top_k: int = 3,
    ) -> list[RetrievedChunk]:
        # 1. Vector search qua ChromaDB
        vector_results = self.vectorstore.query(
            query=query,
            n_results=top_k * 2,  # lấy dư để merge
            where={"language": language} if language else None,
        )

        # 2. BM25 keyword search
        bm25_results = self.bm25_index.search(
            query=query,
            top_k=top_k * 2,
        )

        # 3. Merge + re-rank
        merged = self._merge_and_rerank(
            vector_results, bm25_results, top_k
        )

        return merged
```

### 7.4 Ingestion Pipeline

```text
1. Load document (markdown/text)
2. Split: 512 tokens, 50 token overlap
3. Attach metadata: source, chunk_index, language, doc_type, category
4. Embed → ChromaDB insert
5. Tokenize → BM25 index insert
6. Script: scripts/seed_rag.py chạy trước demo
```

### 7.5 RAG Debug — Dữ liệu cho /ai-debug page

Mỗi `search_coding_standard` call lưu MongoDB `tool_call_logs`:

```json
{
  "tool_name": "search_coding_standard",
  "input": {
    "query": "SQL injection prevention Python SQLAlchemy",
    "language": "python",
    "top_k": 3
  },
  "output": {
    "retrieval_method": "hybrid",
    "vector_results": [...],
    "bm25_results": [...],
    "merged_results": [
      {
        "source": "OWASP A03:2021",
        "content": "Never concatenate user input...",
        "vector_score": 0.89,
        "bm25_score": 0.75,
        "final_score": 0.847
      }
    ]
  },
  "duration_ms": 245
}
```

---

## 8. SYSTEM PROMPT — BẢN CUỐI

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
- Prioritize: security > critical bugs > performance > maintainability > style
- Do NOT suggest refactoring everything — focus on genuine problems
- When reading code, pay attention to static_issues_in_range — validate and enhance them

## Workflow:
1. Call analyze_project_structure to understand the project
2. Read high-priority files using read_file_chunk
3. For security concerns, ALWAYS call search_coding_standard first
4. Generate issues using generate_issue tool (NEVER as free text)
5. After reviewing all relevant files, call generate_final_report

## Output format:
Always use `generate_issue` tool for each issue. Never output issues as free text.
After reviewing all files, call `generate_final_report` to synthesize.
"""
```

---

## 9. OUTPUT CONTRACT — KHỚP VỚI DATABASE

### 9.1 generate_issue → review_issues (PostgreSQL)

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
source          → 'ai_review' (hardcoded)
```

### 9.2 generate_final_report → review_reports (PostgreSQL)

```text
security_score          → review_reports.security_score       (0.0 - 10.0)
maintainability_score   → review_reports.maintainability_score
performance_score       → review_reports.performance_score
overall_score           → review_reports.overall_score
executive_summary       → review_reports.executive_summary
top_priorities          → review_reports.top_risky_files (JSONB)
tech_stack              → review_reports.tech_stack (JSONB)
```

---

## 10. TOOL CALL LOGGING — CHO /AI-DEBUG PAGE

Mỗi tool call (không phân biệt tool nào) → log vào MongoDB `tool_call_logs`:

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

Frontend `/ai-debug` hiển thị:
- Timeline tool calls theo thứ tự sequence
- Mỗi search_coding_standard: query → retrieved chunks + scores
- Mỗi generate_issue: structured input → saved issue
- Tổng thời gian agent session

---

## 11. MÔ TẢ CHO BÁO CÁO / SLIDE

> RepoGuard AI sử dụng phương pháp **Evidence-Grounded Agentic Hybrid RAG** cho bài toán review source code cấp repository.
>
> Khác với Naive RAG chỉ truy xuất top-k chunks bằng vector similarity, hệ thống tách riêng hai loại retrieval:
>
> **(1) Repository Context Retrieval:** Source code được phân tích bằng AST, chia theo function/class boundary, gắn metadata như file_path, language, module, line_start, line_end, risk_area. Khi review, agent dùng metadata filtering để lấy đúng đoạn code cần thiết.
>
> **(2) Knowledge Retrieval:** Coding standards, OWASP Top 10, Python/FastAPI best practices được lưu trong vector database (ChromaDB) kết hợp BM25 keyword search. Khi phát hiện nghi vấn security, agent truy xuất guideline liên quan để kiểm chứng và làm căn cứ.
>
> Ngoài ra, hệ thống kết hợp static analysis tools (Ruff, Bandit, ESLint) để ground kết quả AI review, giảm hallucination. Mỗi issue sinh ra bắt buộc có severity, category, file path, line number, confidence score >= 0.7 và evidence/reference rõ ràng.

---

## 12. MVP vs ADVANCED — RANH GIỚI RÕ RÀNG

### MVP (Bắt buộc trong 4 tuần)

```text
✅ AST-based code chunking (Python)
✅ Metadata cho mỗi chunk (file_path, language, function_name, line_start/end)
✅ ChromaDB vector RAG cho coding standards
✅ BM25 keyword search (rank-bm25, in-memory)
✅ HybridRetriever (vector + BM25 merge)
✅ Static analysis grounding (ruff + bandit)
✅ ReAct agent với 5 tools cố định
✅ Structured issue JSON qua generate_issue tool
✅ Evidence mapping (RAG reference cho security issues)
✅ Confidence threshold >= 0.7
✅ Line number validation
✅ Hard limit 20 tool calls/session
✅ Tool call logging → MongoDB
✅ Direct SDK (google-genai / openai) — không LangChain
```

### Advanced (Nếu kịp, đáng làm)

```text
🔶 Parent-child context expansion tự động
🔶 risk_area auto-classification nâng cao
🔶 RAG debug page /ai-debug
🔶 Issue deduplication thông minh (fuzzy match title)
🔶 Compare 2 lần review
🔶 Multi-language AST (JS/TS via tree-sitter)
```

### KHÔNG LÀM

```text
❌ Full Graph RAG (dependency graph)
❌ Repo-wide call graph
❌ Multi-agent architecture
❌ Semgrep integration
❌ LangChain/LangGraph framework
❌ Fine-tuning embedding model
❌ Private repo OAuth
```

---

## 13. DEPENDENCIES MỚI CẦN THÊM

```text
# Trong requirements.txt
google-genai              # Gemini SDK (tool calling support)
openai                    # Fallback LLM
chromadb                  # Vector DB
sentence-transformers     # Embedding model (all-MiniLM-L6-v2)
rank-bm25                 # BM25 keyword search
```

---

## 14. CHECKLIST TRƯỚC KHI CODE AI MODULE

```text
- [ ] ChromaDB PersistentClient chạy được local
- [ ] seed_rag.py ingest OWASP Top 10 thành công
- [ ] BM25 index build từ cùng documents
- [ ] HybridRetriever search trả kết quả hợp lý (test manual)
- [ ] Gemini SDK tool calling chạy được với 1 tool đơn giản
- [ ] AST chunker parse file Python thành function/class chunks
- [ ] Metadata gắn đúng cho mỗi chunk
- [ ] Agent loop chạy được end-to-end với 1 file test
- [ ] Tool call logs ghi vào MongoDB
- [ ] generate_issue validate confidence >= 0.7
- [ ] Line validation hoạt động
```

---

*Tài liệu này là quyết định cuối cùng cho AI pipeline của RepoGuard. Code theo đúng flow này.*
