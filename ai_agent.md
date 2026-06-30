# AGENT.md — RepoGuard AI / AI Review Agent Module

> File context cho Claude Code khi làm việc trong `backend/app/ai/`.
> Đọc file này trước khi code bất kỳ thứ gì thuộc AI Agent, Tool Calling, RAG.
> Nguồn: `RepoGuard_AI_Project_Plan.md` — section 6 (AI Pipeline), section 7 (RAG Design), Phase 6 milestone.

---

## 1. Vai trò module này trong hệ thống

AI Agent là bước **AI_REVIEWING** trong Review Job Workflow. Nó nhận input từ 3 nguồn (project structure, static analysis results, code chunks), chạy một ReAct loop có tool calling, và xuất ra structured issues + report tổng hợp. Đây không phải là "gọi LLM 1 lần lấy text trả về" — đây là agent thực sự: LLM tự quyết định gọi tool nào, khi nào, với input gì.

```
Input:  project_context + file_chunks + static_analysis_results
Output: List[ReviewIssue] (JSON, qua tool calling) + ReportSummary + Scores
```

Worker gọi vào agent này tại bước `AI_REVIEWING` trong Celery task (`workers/review_worker.py`), sau khi đã có kết quả structure analysis + static analysis + chunking lưu trong MongoDB.

---

## 2. Vị trí file (bám đúng Repository Structure)

```
backend/app/ai/
├── agent.py                 # Main AI Agent — ReAct loop, orchestration
├── tools/
│   ├── __init__.py          # Export tool schemas + dispatch map
│   ├── analyze_structure.py # Tool 1: analyze_project_structure
│   ├── read_file.py         # Tool 2: read_file_chunk
│   ├── search_rag.py        # Tool 3: search_coding_standard (+ search_similar_issues)
│   ├── generate_issue.py    # Tool 4: generate_issue
│   └── generate_report.py   # Tool 5: generate_final_report
├── prompts.py                # REVIEW_SYSTEM_PROMPT + helper prompt builders
└── rag/
    ├── vectorstore.py        # ChromaDB client wrapper (singleton)
    ├── ingestion.py           # RAGIngestionPipeline — dùng bởi scripts/seed_rag.py
    └── retriever.py           # Search logic, top_k, metadata filter

backend/app/analyzers/code_chunker.py   # AST-based chunker, KHÔNG nằm trong ai/
```

Không tạo thêm file ngoài cấu trúc này trừ khi thật cần thiết — giữ đúng layout để khớp với WBS và tránh review lệch khi mentor đọc code.

---

## 3. LLM Client

| Thành phần | Giá trị |
|---|---|
| Primary | Google Gemini 2.0 Flash (Google AI Studio free tier) |
| Fallback | OpenAI `gpt-4o-mini` |
| Client | Direct SDK hoặc LangChain — **phải hỗ trợ tool calling/function calling** |
| Retry | Exponential backoff khi gặp timeout/rate limit |
| Config | Đọc API key từ `.env` qua `app/core/config.py`, không hardcode |

Khi viết `agent.py`, thiết kế interface LLM client là abstraction riêng (vd `LLMClient` base class) để switch Gemini ↔ OpenAI mà không sửa logic agent loop.

---

## 4. Code Chunking Strategy

Chunk theo ranh giới ngữ nghĩa, **không phải fixed-line splitting** trừ khi bắt buộc:

1. Class boundary
2. Function boundary
3. Nếu function quá dài → chunk theo block (if/for/with)
4. Fallback: fixed 60 lines, overlap 10 lines

```
MAX_TOKENS_PER_CHUNK = 1500   # ~400-500 dòng code
OVERLAP_LINES = 10
```

Python dùng `ast` module để parse class/function boundary (`ast.walk` + `ast.get_source_segment`). Mỗi chunk lưu metadata vào MongoDB collection `chunk_metadata` (file_path, chunk_index, total_chunks, line_start/end, token_count, language, has_functions, has_classes).

---

## 5. Tool Calling Schema — 5 tools cố định

Đây là contract cố định, **không tự đổi tên field hoặc enum** vì frontend `/ai-debug` page và DB schema phụ thuộc vào đúng shape này.

### Tool 1 — `analyze_project_structure`
Input: `{job_id}` → Output: languages, frameworks, total_files, `files_to_review` (path + priority + reason), static_analysis_summary theo tool.

### Tool 2 — `read_file_chunk`
Input: `{job_id, file_path, chunk_index}` → Output: content, line_start/end, total_chunks, `static_issues_in_range` (nối kết quả ruff/bandit theo dòng).

### Tool 3 — `search_coding_standard`
Input: `{query, language?, top_k=3}` → Output: `results[]` gồm source, content, similarity_score. Đây là RAG lookup — bắt buộc gọi trước khi flag security issue (xem mục 7).

### Tool 4 — `generate_issue`
Input bắt buộc: `file_path, severity (critical|high|medium|low|info), category (security|bug|performance|maintainability|style), title, description, confidence (0-1)`. Optional: `line_start, line_end, suggestion, references[]`.
**AI không bao giờ tự generate issue dưới dạng free text — phải đi qua tool này.**

### Tool 5 — `generate_final_report`
Input: `{job_id, all_issues[]}` → Output: security_score, maintainability_score, performance_score, overall_score, executive_summary, top_priorities[].

Khi code các tool này: mỗi tool là 1 function async, input/output validate bằng Pydantic schema riêng (đặt cùng file hoặc trong `app/schemas/ai_tools.py`), và tool dispatch map trong `tools/__init__.py` map `tool_name → callable`.

---

## 6. ReAct Loop — luồng agent

```
1. PLAN     → gọi analyze_project_structure
2. READ     → gọi read_file_chunk cho từng file trong files_to_review (ưu tiên priority=high trước)
3. RAG      → khi nghi ngờ security/pattern lạ, gọi search_coding_standard
4. ISSUE    → gọi generate_issue cho mỗi vấn đề tìm thấy (confidence >= 0.7)
5. REPORT   → sau khi review xong tất cả file, gọi generate_final_report
```

Agent loop dừng khi: (a) đã gọi `generate_final_report`, (b) đạt **hard limit 20 tool calls/session**, hoặc (c) lỗi không recover được → set job FAILED.

Mỗi tool call phải log vào MongoDB `tool_call_logs` (job_id, session_id, sequence, tool_name, called_at, duration_ms, input, output) — đây là dữ liệu nuôi trang `/ai-debug`, không được skip.

---

## 7. System Prompt — khung cố định

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
- Use `search_coding_standard` tool BEFORE flagging any security issue
- Only generate issues where confidence >= 0.7
- Never flag issues you are not sure about — false positives waste developer time
- For each issue, cite the specific line numbers
- Prioritize: security > critical bugs > performance > maintainability > style
- Do NOT suggest refactoring everything — focus on genuine problems

## Output format:
Always use `generate_issue` tool for each issue. Never output issues as free text.
After reviewing all files, call `generate_final_report` to synthesize.
"""
```

Không nới lỏng rule "confidence >= 0.7" hoặc rule "luôn dùng tool, không free text" — đây là cơ chế chống hallucination chính, mentor sẽ hỏi (xem Q4 trong project plan).

---

## 8. RAG Design

### Nguồn knowledge base
OWASP Top 10 2021, Python Best Practices (PEP 8/20), FastAPI Best Practices, Clean Code Principles, Security Checklist, repo's own README/CONTRIBUTING (extract lúc clone).

### Vector DB — ChromaDB
```python
import chromadb
client = chromadb.PersistentClient(path="/data/chromadb")
collection = client.get_or_create_collection(
    name="coding_standards",
    metadata={"hnsw:space": "cosine"}
)
```
Embedding model: `sentence-transformers/all-MiniLM-L6-v2` — free, chạy CPU, không cần GPU/cloud.

### Ingestion
`rag/ingestion.py` implement `RAGIngestionPipeline`: load document → split 512 tokens/50 overlap → attach metadata (source, chunk_index, language, doc_type, ingested_at) → embed + add vào ChromaDB. Script `scripts/seed_rag.py` gọi pipeline này để ingest toàn bộ knowledge base trước khi demo — **chạy script này sớm, test với 10 documents trước khi ingest full** (đây là phần HIGH RISK theo self-assessment, dễ bị retrieval quality thấp).

### Khi nào agent retrieve
- Phát hiện potential security issue → `search_coding_standard(query=issue_description, language=lang)`
- Review file liên quan auth/crypto → tự động retrieve OWASP guidelines
- Gặp pattern không chắc chắn → `search_similar_issues(pattern=code_pattern)`

### RAG Debug Page (`/ai-debug`)
Mỗi `search_coding_standard` call cần lưu đủ: query input, top-3 retrieved chunks + similarity score, source document name — để frontend hiển thị timeline tool calls + RAG retrievals khi demo cho mentor.

---

## 9. Anti-Hallucination — không thương lượng

| Kỹ thuật | Cách làm | Code ở đâu |
|---|---|---|
| Grounding bằng RAG | Bắt AI search OWASP trước khi flag security issue | system prompt + `search_coding_standard` |
| Confidence threshold | Chỉ lưu issue có `confidence >= 0.7` | validate trong `generate_issue.py` trước khi ghi DB |
| Structured output | AI không tự sinh text — phải dùng `generate_issue` tool | enforce qua tool calling, reject free-text response |
| Cross-validate static tools | AI flag nhưng static tool không có → giảm severity | logic ở `report_service.py` lúc merge issues |
| Line number validation | Backend validate line_start/end tồn tại trong file | validate trước khi insert `review_issues` |
| Hard limit tool calls | Tối đa 20 tool calls/session | enforce trong agent loop, không phải prompt instruction |

Nếu Claude Code đang implement mà thấy mình sắp bỏ qua 1 trong 6 dòng trên để "cho chạy nhanh hơn" — dừng lại, đây chính là phần mentor sẽ hỏi kỹ nhất (Q4).

---

## 10. Output Contract — khớp với DB

`generate_issue` output map trực tiếp vào bảng `review_issues` (PostgreSQL): `file_path, line_start, line_end, severity, category, title, description, suggestion, source='ai_review', confidence, raw_output`.

`generate_final_report` output map vào `review_reports`: `security_score, maintainability_score, performance_score, overall_score, executive_summary, top_risky_files, tech_stack`.

Không đổi tên field khi code service layer — giữ đúng tên cột đã định trong schema để tránh phải viết thêm lớp mapping không cần thiết.

---

## 11. Definition of Done (theo Phase 6 milestone)

- [ ] Agent chạy end-to-end với ít nhất 2 test repo thật
- [ ] `tool_call_logs` trong MongoDB có đủ trace từng bước
- [ ] `review_issues` chỉ chứa issue confidence ≥ 0.7
- [ ] RAG retrieve đúng OWASP content khi review code liên quan security
- [ ] `/ai-debug` hiển thị được tool calls + RAG results
- [ ] Hard limit 20 tool calls/session hoạt động đúng (test bằng repo cố tình phức tạp)

## 12. Rủi ro đã biết — đừng ngạc nhiên

- LLM API timeout/rate limit → dùng Gemini Flash (free tier rộng hơn), retry exponential backoff
- ChromaDB retrieval quality thấp lúc đầu → bình thường, test với 10 documents, tune `top_k` trước khi ingest full knowledge base
- Agent lặp tool call vô tận nếu thiếu hard limit → enforce limit ở code, không tin vào system prompt
