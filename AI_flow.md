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
├── Roadmap Compliance:  Deterministic Rule Engine (opt-in, ưu tiên cao nhất, KHÔNG dùng AI)
├── Code Retrieval:      AST Chunking + Metadata Filtering + Parent-Child Context
├── Knowledge Retrieval: Hybrid RAG (Vector Search + Keyword Matching)
├── Agent Pattern:       ReAct Tool Calling (tự quyết tool → tự truyền input → tự dùng output)
├── Grounding:           Static Analysis Cross-Check + Evidence Mapping
└── Output:              Structured JSON (generate_issue tool, không free text)
```

### 1.3 Vai trò đúng của từng thành phần

| Thành phần | Vai trò | KHÔNG PHẢI là |
|---|---|---|
| Roadmap Compliance Rule Engine | Đối chiếu repo với checklist **bắt buộc phải có** (mục 3) — chạy trước tất cả, confidence=1.0 | "1 loại issue AI review" |
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
  → Dùng: Python AST chunking + metadata filtering + parent/neighbor context hints

Lớp 2: Coding Standard Retrieval (Knowledge Base)
  → Tìm coding standards, OWASP, best practices
  → Dùng: Hybrid RAG (vector search + keyword matching)
```

**Tại sao tách?** Vì source code và tài liệu chuẩn có bản chất khác nhau:
- Code cần metadata chính xác (file_path, line_start, function_name)
- Tài liệu chuẩn cần semantic search + keyword matching

### 2.2 Lớp 1 — Repository Context Retrieval

#### Code Chunking

```text
File code
→ Python: parse AST bằng ast module, tách theo class/function/file boundary
→ Non-Python/plain text: chunk theo file/range an toàn
→ Future enhancement: JS/TS AST bằng acorn/tree-sitter nếu cần review sâu frontend
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

Đây là logic của bước build `chunk_review_plan`: chọn target chunks bằng metadata
filtering, static findings, roadmap verification, high-risk paths, và breadth sample.
Khi agent đọc một target chunk, `read_file_chunk` trả metadata kèm theo
`static_issues_in_range`.

#### Parent-Child Context Expansion

```text
Khi AI đang đọc 1 function chunk:
  → Nếu cần hiểu context rộng hơn
  → read_file_chunk trả context_hints gồm neighbor_chunk_indexes và parent_chunk_indexes
  → Agent gọi read_file_chunk với chunk_index được hint nếu cần mở rộng context

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

## 3. ROADMAP COMPLIANCE RULE ENGINE — LỚP KIỂM TRA ƯU TIÊN CAO NHẤT

> Đây là lớp check **độc lập với AI**, chạy **trước** static analysis và AI review. Mục đích: đối chiếu repo được review với danh sách công nghệ/tính năng **bắt buộc phải có** theo `Lộ_trình_đào_tạo_Python_NextJS_AI_agent.xlsx` (Tuần 1–8). Ví dụ: lộ trình Tuần 5 yêu cầu WebSocket/SSE — nếu repo không có, đây phải là 1 issue, không phải "AI quên nói".

### 3.1 Vì sao tách riêng, không để AI tự phát hiện

- AI review dựa trên đọc code → **không đảm bảo** phát hiện được cái gì **KHÔNG tồn tại** trong repo (absence detection). AI giỏi phân tích cái đang có, dễ bỏ sót cái đang thiếu vì không có tín hiệu nào để "trigger" suy luận.
- Việc "repo có thiếu WebSocket không" là câu hỏi **có/không dựa trên bằng chứng file/dependency/pattern** — không cần LLM suy luận, dùng rule engine deterministic là đủ và đáng tin cậy hơn (confidence = 1.0, giống static analysis).
- Vì đối chiếu với **lộ trình đào tạo cụ thể**, rule set này **không áp dụng cho mọi repo** RepoGuard review (một repo Node.js ngẫu nhiên trên GitHub không có nghĩa vụ phải có WebSocket theo lộ trình này). Do đó:

```text
Quyết định kiến trúc: RULE PROFILE — OPT-IN THEO JOB

review_jobs.options.rule_profile:
  null → chỉ chạy Static Analysis + AI Review (mặc định, dùng cho mọi repo)
  {
    "id": "roadmap_bootcamp_v1",
    "weeks_included": [1, 2, 3]   // null/omit = TẤT CẢ tuần (dùng cho final project)
  }

→ Người dùng chọn rule_profile khi tạo review job (dropdown ở FE, mặc định = null).
→ Không hard-code rule này chạy cho tất cả job — giữ RepoGuard tổng quát đúng mục tiêu ban đầu.
```

⚠️ **Lý do bắt buộc phải có `weeks_included`:** repo nộp cho bài tập Tuần 1 (chỉ học JWT auth) chưa hề được dạy WebSocket (Tuần 5) hay RAG (Tuần 7) — nếu chạy toàn bộ 79 rule vào 1 bài nộp Tuần 1, hệ thống sẽ báo "thiếu" hàng loạt thứ học viên chưa học tới, sai hoàn toàn mục đích chấm điểm. Rule luôn có field `week` (mục 3.4) để filter:

```text
Filter logic: rule.week in weeks_included OR rule.week == "GEN" (GEN luôn áp dụng)
- Review bài nộp theo tuần   → weeks_included = [1..N tuần đã học, cumulative]
- Review final project (T8)  → weeks_included = null (áp dụng toàn bộ, vì đề bài yêu cầu
  "Tổng hợp toàn bộ stack") — riêng RC-GEN-03 (kết hợp ≥2/3 DB) đã để priority=P2 vì
  final project liệt kê rõ đây là "điểm cộng", không bắt buộc.
```

### 3.2 Rule Schema — Cấu trúc 1 rule

```json
{
  "rule_id": "RC-W5-01",
  "week": 5,
  "skill_group": "Realtime Communication",
  "requirement": "Repo phải triển khai WebSocket hoặc SSE, broadcast được nhiều user",
  "check_type": "required_code_pattern",
  "target": {
    "glob": "**/*.py",
    "pattern": "websocket|WebSocket|text/event-stream|EventSourceResponse",
    "min_matches": 1
  },
  "priority": "P0",
  "severity_if_missing": "critical",
  "category": "requirement",
  "source": "roadmap_rule",
  "confidence": 1.0,
  "rationale_if_missing": "Lộ trình Tuần 5 (Realtime Communication) yêu cầu SSE hoặc WebSocket — không tìm thấy pattern liên quan trong codebase.",
  "needs_ai_verification": true,
  "ai_hint": "Xác nhận broadcast được tới NHIỀU client đồng thời, không phải chỉ echo 1-1 giữa 1 client và server."
}
```

**8 loại `check_type` được hỗ trợ:**

| check_type | Ý nghĩa | Ví dụ target |
|---|---|---|
| `required_file` | 1 file cụ thể phải tồn tại | `alembic.ini` |
| `required_any_of` | Ít nhất 1 trong danh sách path phải tồn tại | `["Dockerfile.dev", "docker/Dockerfile.dev"]` |
| `required_folder` | 1 thư mục phải tồn tại | `routers/` |
| `forbidden_tracked_file` | File KHÔNG được nằm trong git-tracked files (dù tồn tại local do .gitignore) | `.env` |
| `required_dependency` | Package phải khai báo trong manifest (requirements.txt / pyproject.toml / package.json) | `fastapi`, `next` |
| `required_code_pattern` | Regex/keyword phải xuất hiện ≥ N lần trong file khớp glob | `pattern="ConnectionManager"` |
| `min_file_count` | Số file khớp glob trong 1 thư mục phải ≥ N | `alembic/versions/*.py`, min=2 |
| `required_config_key` | 1 file config cụ thể phải chứa key/directive | `docker-compose.yml` chứa `healthcheck:` |

### 3.2b `needs_ai_verification` — Ranh giới Existence vs Correctness

> Đây là điểm sửa quan trọng nhất so với bản v1: rule engine (regex/dependency/file check) **chỉ trả lời được "CÓ tồn tại hay không"**. Nó **không bao giờ** trả lời được "có LÀM ĐÚNG hay không" — vd tìm thấy chữ `"sync"` trong `workers/` không chứng minh job đó thực sự chạy định kỳ và xử lý đúng incremental sync theo timestamp. 16/79 rule (đánh dấu ⚠️ ở mục 3.4) rơi vào nhóm này.

```text
Xử lý khi rule.needs_ai_verification == true:

1. Deterministic check FAIL (không tìm thấy pattern/dependency)
   → FAIL luôn, KHÔNG cần AI. Ghi issue category=requirement, source=roadmap_rule,
     confidence=1.0 — giống hệt rule thường (bằng chứng "không tồn tại" là tuyệt đối,
     không cần AI xác nhận thêm).

2. Deterministic check PASS (tìm thấy pattern/dependency)
   → KHÔNG ghi issue ngay, trạng thái = "provisional_pass"
   → (file_path, rule_id, ai_hint) được đẩy vào roadmap_verification_queue
   → File đó BẮT BUỘC nằm trong danh sách file Agent phải đọc (mục 4.2, Phase 7),
     kèm ai_hint làm gợi ý cho Agent biết cần xác minh điều gì
   → Agent tự đọc code và quyết định:
       a. Logic đúng như ai_hint mô tả → không tạo issue gì thêm, rule coi như pass
       b. Logic sai/thiếu → generate_issue với category=maintainability|bug|security
          (KHÔNG phải category=requirement — vì đây là nhận định của AI, không còn
          confidence=1.0 tuyệt đối), description PHẢI ghi rõ:
          "Phát hiện khi verify roadmap rule RC-Wx-xx: <mô tả cụ thể>"

→ compliance_score (mục 3.5) tính provisional_pass NHƯ PASS bình thường — vì đây vẫn
  là con số deterministic dựa trên bằng chứng tồn tại. Nếu Agent sau đó phát hiện lỗi,
  nó xuất hiện như 1 issue AI riêng, KHÔNG lùi lại sửa compliance_score đã tính.
```

### 3.3 File Structure Module

```text
backend/app/ai/rules/
├── roadmap_checker.py       # RoadmapComplianceChecker — engine chạy 8 check_type ở trên
├── roadmap_rules_v2.yaml    # Bộ rule chính thức (nội dung ở mục 3.4, 79 rules)
└── git_utils.py             # git ls-files wrapper cho forbidden_tracked_file
```

```python
class RoadmapComplianceChecker:
    """
    Chạy độc lập, KHÔNG gọi LLM. Đọc rules từ roadmap_rules_v2.yaml,
    filter theo weeks_included (mục 3.1), áp vào sandbox repo, trả về
    danh sách RuleResult (pass/fail/provisional_pass cho MỌI rule áp dụng —
    dùng để tính compliance_score) + verification_queue cho Agent.
    """
    def run(self, sandbox_path: str, rule_profile: dict) -> RoadmapCheckOutput:
        weeks = rule_profile.get("weeks_included")  # None = tất cả
        rules = self._load_rules(weeks_included=weeks)
        results, verification_queue = [], []
        for rule in rules:
            found = self._evaluate(rule, sandbox_path)  # bool: pattern/file/dep có tồn tại?
            if not found:
                status = "fail"
            elif rule.needs_ai_verification:
                status = "provisional_pass"
                verification_queue.append(VerificationItem(
                    rule_id=rule.rule_id, file_path=self._matched_file(rule, sandbox_path),
                    ai_hint=rule.ai_hint,
                ))
            else:
                status = "pass"
            results.append(RuleResult(
                rule_id=rule.rule_id, status=status,
                severity=None if status != "fail" else rule.severity_if_missing,
                week=rule.week, skill_group=rule.skill_group,
            ))
        return RoadmapCheckOutput(results=results, verification_queue=verification_queue)
```

### 3.4 Bộ Rule Chính Thức — Roadmap Compliance Rule Set v2

> Nguồn: `Lộ_trình_đào_tạo_Python_NextJS_AI_agent.xlsx`, đọc lại đầy đủ nội dung chi tiết + bảng tiêu chí chấm điểm từng tuần (không chỉ sheet Mục tiêu). Priority: **P0** = kỹ năng cốt lõi tuần đó · **P1** = yêu cầu rõ trong tiêu chí chấm nhưng không chặn · **P2** = điểm cộng/tự học. `Severity nếu thiếu`: P0→critical, P1→high, P2→low.

> Cột **AI?** = `⚠️` nghĩa là rule này có `needs_ai_verification: true` (mục 3.2b) — deterministic check chỉ xác nhận được **sự tồn tại**, còn **tính đúng đắn** (correctness) bắt buộc AI Agent đọc code và xác nhận thêm. Không đánh dấu = check tồn tại là đủ, không cần AI.

#### Tuần 1 — Backend Core & JWT Auth

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W1-01 | Dùng FastAPI | `required_dependency` | fastapi | P0 | — |
| RC-W1-02 | Dùng Pydantic (schema validation) | `required_dependency` | pydantic | P0 | — |
| RC-W1-03 | Dùng Uvicorn | `required_dependency` | uvicorn | P0 | — |
| RC-W1-04 | Có thư viện JWT | `required_dependency` | pyjwt hoặc python-jose | P0 | — |
| RC-W1-05 | Tách thư mục routers/ | `required_folder` | routers/ | P0 | — |
| RC-W1-06 | Tách thư mục schemas/ | `required_folder` | schemas/ | P0 | — |
| RC-W1-07 | Tách thư mục models/ | `required_folder` | models/ | P0 | — |
| RC-W1-08 | Tách thư mục utils/ (jwt_handler, password_hash) | `required_folder` | utils/ | P1 | — |
| RC-W1-09 | Endpoint /register (password phải hash) | `required_code_pattern` | "/register" | P0 | — |
| RC-W1-10 | Endpoint /login trả access+refresh token | `required_code_pattern` | "/login" + "refresh_token" | P0 | ⚠️ |
| RC-W1-11 | Endpoint /token/refresh | `required_code_pattern` | "/token/refresh" hoặc "/refresh" | P0 | ⚠️ |
| RC-W1-12 | Endpoint /users/me | `required_code_pattern` | "/users/me" | P1 | — |
| RC-W1-13 | Có logout / vô hiệu hoá token | `required_code_pattern` | "/logout" hoặc "blacklist" | P1 | ⚠️ |
| RC-W1-14 | Có logging | `required_code_pattern` | import logging hoặc loguru | P1 | — |
| RC-W1-15 | Có exception handler chuẩn REST | `required_code_pattern` | HTTPException hoặc exception_handler | P1 | — |
| RC-W1-16 | README hướng dẫn setup | `required_file` | README.md | P1 | — |
| RC-W1-17 | Password được hash trước khi lưu DB | `required_dependency` | bcrypt / passlib / argon2 | P0 | ⚠️ |

**Ghi chú AI-verify (tuần 1):**
- `RC-W1-10`: Xác nhận /login thực sự verify password hash và sinh đúng JWT 2 tầng (access + refresh) với payload/expiry hợp lệ — không phải hardcode token giả để pass check.
- `RC-W1-11`: Xác nhận endpoint validate refresh_token (chữ ký, hạn dùng, chưa revoke) TRƯỚC khi cấp access_token mới — không cấp vô điều kiện.
- `RC-W1-13`: Xác nhận logout thực sự vô hiệu hoá token (xoá refresh_token khỏi DB/Redis hoặc thêm blacklist) — không chỉ trả 200 OK mà không làm gì.
- `RC-W1-17`: Xác nhận password THỰC SỰ được hash tại điểm gọi trong route /register trước khi insert DB — không lưu plaintext dù có import thư viện hash.

#### Tuần 2 — DevOps & Docker

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W2-01 | Dockerfile.dev cho Backend | `required_any_of` | backend/Dockerfile.dev | P0 | — |
| RC-W2-02 | Dockerfile.prod cho Backend | `required_any_of` | backend/Dockerfile.prod | P0 | — |
| RC-W2-03 | Dockerfile.dev cho Frontend | `required_any_of` | frontend/Dockerfile.dev | P0 | — |
| RC-W2-04 | Dockerfile.prod cho Frontend | `required_any_of` | frontend/Dockerfile.prod | P0 | — |
| RC-W2-05 | docker-compose.yml ở root | `required_any_of` | docker-compose.yml | P0 | — |
| RC-W2-06 | Compose có service DB (MySQL/Mongo/Redis) | `required_config_key` | service DB trong compose | P0 | — |
| RC-W2-07 | Có Nginx reverse proxy config | `required_file` | nginx/nginx.conf | P1 | — |
| RC-W2-08 | .env KHÔNG được commit | `forbidden_tracked_file` | .env | P0 | — |
| RC-W2-09 | Có .env.example | `required_file` | .env.example | P1 | — |
| RC-W2-10 | Compose có healthcheck | `required_config_key` | healthcheck: | P2 | — |
| RC-W2-11 | Nginx có cấu hình SSL/TLS | `required_config_key` | ssl_certificate | P2 | — |
| RC-W2-12 | README có link demo triển khai (Render/Railway/EC2) | `required_code_pattern` | URL pattern trong README | P2 | — |

#### Tuần 3 — Database Layer (MySQL + MongoDB + Redis + Alembic)

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W3-01 | Dùng SQLAlchemy ORM | `required_dependency` | sqlalchemy | P0 | — |
| RC-W3-02 | Dùng Alembic migration | `required_dependency + required_folder` | alembic, alembic/ | P0 | — |
| RC-W3-03 | Có ≥ 2 revision Alembic | `min_file_count` | alembic/versions/*.py, min=2 | P0 | — |
| RC-W3-04 | Dùng MongoDB (PyMongo/Motor) | `required_dependency` | pymongo hoặc motor | P0 | — |
| RC-W3-05 | Dùng Redis client | `required_dependency` | redis hoặc aioredis | P0 | — |
| RC-W3-06 | Cache GET /products với TTL | `required_code_pattern` | expire / setex / ttl | P0 | ⚠️ |
| RC-W3-07 | Cache bị xoá khi có CRUD thay đổi dữ liệu | `required_code_pattern` | delete/invalidate cache trong route POST/PUT/DELETE | P0 | ⚠️ |
| RC-W3-08 | Có RBAC (Role, Permission, Role_Permission) | `required_code_pattern` | model Role + Permission | P1 | — |
| RC-W3-09 | Có job sync định kỳ MySQL → MongoDB | `required_code_pattern` | "sync" trong workers/ hoặc services/ | P0 | ⚠️ |
| RC-W3-10 | Lưu lịch sử sync vào collection sync_logs | `required_code_pattern` | "sync_logs" | P1 | — |
| RC-W3-11 | Alembic có revision loại create table VÀ alter table | `required_code_pattern` | op.create_table + op.alter_column/op.add_column | P1 | — |
| RC-W3-12 | Có benchmark cache vs không cache | `required_code_pattern` | "benchmark" hoặc log thời gian truy vấn | P2 | — |

**Ghi chú AI-verify (tuần 3):**
- `RC-W3-06`: Xác nhận TTL cụ thể ~30s theo đề bài, và cache áp dụng đúng cho response GET /products (không phải cache 1 endpoint không liên quan để pass check).
- `RC-W3-07`: Xác nhận cache bị xoá NGAY khi tạo/sửa/xoá sản phẩm — không phải chỉ hết hạn tự nhiên theo TTL.
- `RC-W3-09`: Xác nhận: (a) job chạy ĐỊNH KỲ thật (celery beat/cron/scheduler, không phải hàm gọi tay); (b) dùng timestamp/version để chỉ đồng bộ phần THAY ĐỔI (incremental) — không xoá-ghi-lại toàn bộ mỗi lần chạy.

#### Tuần 4 — Frontend Next.js 15

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W4-01 | Dùng Next.js 15 | `required_dependency` | next ^15 trong package.json | P0 | — |
| RC-W4-02 | Dùng TailwindCSS | `required_dependency` | tailwindcss | P0 | — |
| RC-W4-03 | Dùng Axios | `required_dependency` | axios | P1 | — |
| RC-W4-04 | Dùng App Router | `required_folder` | app/ với app/layout.tsx | P0 | — |
| RC-W4-05 | Có trang Login + Protected Route | `required_code_pattern` | middleware.ts hoặc auth guard | P0 | ⚠️ |
| RC-W4-06 | Dùng TypeScript | `required_file` | tsconfig.json | P1 | — |
| RC-W4-07 | Có biểu đồ (chart) | `required_dependency` | recharts hoặc chart.js | P1 | — |
| RC-W4-08 | Có sơ đồ Mermaid | `required_dependency` | mermaid | P2 | — |
| RC-W4-09 | Bảng dữ liệu lớn: pagination/infinite/virtual scroll | `required_dependency` | @tanstack/react-table, react-window, hoặc react-virtualized | P1 | — |
| RC-W4-10 | Có Skeleton loading | `required_code_pattern` | skeleton (case-insensitive) | P2 | — |
| RC-W4-11 | Có .env.local hoặc .env.local.example | `required_any_of` | .env.local, .env.local.example | P1 | — |

**Ghi chú AI-verify (tuần 4):**
- `RC-W4-05`: Xác nhận middleware/guard THỰC SỰ redirect khi chưa đăng nhập — không chỉ được import nhưng chưa gắn vào route nào.

#### Tuần 5 — Realtime (SSE / WebSocket / WebRTC)

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W5-01 | Có SSE hoặc WebSocket, broadcast nhiều user | `required_code_pattern` | websocket/WebSocket hoặc text/event-stream | P0 | ⚠️ |
| RC-W5-02 | Có Connection Manager quản lý nhiều client | `required_code_pattern` | ConnectionManager hoặc pool tương đương | P0 | ⚠️ |
| RC-W5-03 | JWT được validate khi connect realtime | `required_code_pattern` | verify token trong handler WS/SSE | P0 | ⚠️ |
| RC-W5-04 | Redis Pub/Sub đồng bộ multi-instance | `required_code_pattern` | publish( và subscribe( với redis | P1 | ⚠️ |
| RC-W5-05 | Tin nhắn/realtime data được lưu DB | `required_code_pattern` | model Message/ChatLog (MySQL hoặc MongoDB) | P1 | — |
| RC-W5-06 | Frontend có hook/client kết nối realtime | `required_code_pattern` | useWebSocket, EventSource, hoặc tương đương | P1 | — |
| RC-W5-07 | (Tự học) WebRTC video call | `required_code_pattern` | RTCPeerConnection | P2 | — |

**Ghi chú AI-verify (tuần 5):**
- `RC-W5-01`: Xác nhận broadcast được tới NHIỀU client đồng thời, không phải chỉ echo 1-1 giữa 1 client và server.
- `RC-W5-02`: Xác nhận có cấu trúc lưu danh sách connections (dict/list theo room hoặc user) — không phải 1 biến global single-connection.
- `RC-W5-03`: Xác nhận server TỪ CHỐI kết nối khi token invalid (đóng connection / close code) — không chỉ decode token rồi bỏ qua lỗi nếu decode fail.
- `RC-W5-04`: Xác nhận publish/subscribe dùng CHUNG channel và message thực sự được broadcast tới client ở instance khác — không chỉ khai báo publish/subscribe riêng lẻ không khớp channel.

#### Tuần 6 — AI Chatbox với Context Memory

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W6-01 | Lịch sử hội thoại lưu theo user_id/session_id | `required_code_pattern` | model/collection chat_history có field user_id | P0 | — |
| RC-W6-02 | Có cơ chế memory (buffer/summary) | `required_code_pattern` | Memory, ConversationBuffer, hoặc tự viết tương đương | P0 | ⚠️ |
| RC-W6-03 | Có tool/function calling schema cho AI | `required_code_pattern` | tools= hoặc function_call trong AI client | P1 | — |
| RC-W6-04 | Có endpoint reset/clear session | `required_code_pattern` | /reset hoặc /clear trong chat router | P1 | ⚠️ |
| RC-W6-05 | UI chat realtime (dùng lại tuần 5) | `required_code_pattern` | component chat dùng SSE/WebSocket | P1 | — |
| RC-W6-06 | Có logging hội thoại/lỗi | `required_code_pattern` | log trong chat service | P2 | — |
| RC-W6-07 | UI phân biệt AI vs User message + hiệu ứng loading/typing | `required_code_pattern` | role === 'assistant'/'user' hoặc tương đương | P2 | — |

**Ghi chú AI-verify (tuần 6):**
- `RC-W6-02`: Đây là tiêu chí nặng nhất tuần 6 (25%). Xác nhận context được nối/tóm tắt HỢP LÝ khi hội thoại dài — KHÔNG lặp lại nội dung cũ thừa mỗi lần gọi LLM, không phải chỉ nối chuỗi thô không giới hạn.
- `RC-W6-04`: Xác nhận endpoint thực sự xoá context đã lưu (DB/Redis) — không chỉ trả 200 OK mà dữ liệu cũ vẫn còn.

#### Tuần 7 — RAG (Retrieval-Augmented Generation)

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-W7-01 | Dùng Vector DB | `required_dependency` | chromadb, faiss-cpu, hoặc qdrant-client | P0 | — |
| RC-W7-02 | Có embedding pipeline | `required_code_pattern` | embed/Embeddings trong ai/ | P0 | — |
| RC-W7-03 | Có chunking/text splitter | `required_code_pattern` | TextSplitter/chunk trong ai/ | P0 | — |
| RC-W7-04 | Có pipeline retrieve → LLM → response | `required_code_pattern` | retriev (retrieve/retriever) gần lời gọi LLM | P0 | ⚠️ |
| RC-W7-05 | Top-K / similarity threshold cấu hình được | `required_code_pattern` | top_k hoặc similarity_threshold | P1 | — |
| RC-W7-06 | Kết hợp Memory + RAG | `required_code_pattern` | cả memory module và retriever cùng được gọi trong 1 flow | P1 | ⚠️ |
| RC-W7-07 | Có debug mode xem context + RAG result | `required_code_pattern` | route/log trả retrieved_chunks hoặc tương đương | P2 | — |
| RC-W7-08 | Cache câu hỏi lặp lại bằng Redis | `required_code_pattern` | cache key theo query hash trong RAG service | P2 | — |

**Ghi chú AI-verify (tuần 7):**
- `RC-W7-04`: Xác nhận kết quả retrieve THỰC SỰ được đưa vào prompt gửi LLM — không phải gọi retriever nhưng bỏ qua kết quả (retrieve xong không dùng).
- `RC-W7-06`: Xác nhận CẢ HAI (memory hội thoại + RAG retrieval) cùng góp mặt trong 1 lượt trả lời — không phải chỉ dùng 1 trong 2 rồi gọi là hybrid.

#### General / Final Project

| Rule ID | Yêu cầu | check_type | Target / Pattern | Priority | AI? |
|---|---|---|---|---|---|
| RC-GEN-01 | Backend viết bằng Python | `required_dependency` | requirements.txt hoặc pyproject.toml tồn tại | P0 | — |
| RC-GEN-02 | Có .gitignore loại trừ .env, node_modules/, venv/ | `required_config_key` | .gitignore chứa 3 pattern | P1 | — |
| RC-GEN-03 | Kết hợp ≥ 2/3 DB (SQL/Mongo/Redis) — bonus final project | `required_config_key` | ≥ 2 service DB trong docker-compose.yml | P2 | — |
| RC-GEN-04 | Có CI cơ bản | `required_folder` | .github/workflows/ | P2 | — |
| RC-GEN-05 | docker-compose up chạy toàn bộ stack 1 lệnh | `required_config_key` | compose đủ service FE+BE+DB(+Nginx) | P1 | — |
> **Không đưa vào rule engine tĩnh** (cần đo động/runtime hoặc đánh giá chủ quan, ngoài phạm vi file-based check — mentor/AI đánh giá thủ công qua demo hoặc test set câu hỏi thật):
> - Dung lượng Docker image < 400MB (cần build image thật để đo)
> - HTTPS có chứng chỉ SSL thật hoạt động (cần request thật tới domain)
> - App thực sự chạy được trong container (`docker-compose up` không lỗi — cần runtime test)
> - Giảm token usage < 50% so với tuần 6, đo thời gian phản hồi RAG (cần benchmark runtime với dữ liệu thật)
> - AI trả lời "đúng thông tin"/"không hallucinate" (cần test set câu hỏi + so sánh đáp án chuẩn)
> - Git commit history có ý nghĩa (cần phân tích `git log`, không phải file-based)
> - Kỹ năng trình bày/bảo vệ bài làm (chỉ con người đánh giá được)

**Tổng cộng: 79 rules** — 40×P0 (critical nếu thiếu) · 26×P1 (high nếu thiếu) · 13×P2 (low, bonus) — trong đó **16 rules có `needs_ai_verification: true`** (cơ chế ở mục 3.2b).

### 3.5 Compliance Scoring & Priority Override

```text
compliance_score = (số rule PASS + provisional_pass / tổng số rule P0+P1 áp dụng) × 100
bonus_score      = (số rule P2 PASS / tổng số rule P2 áp dụng) × 100

provisional_pass TÍNH NHƯ PASS trong compliance_score (mục 3.2b) — vì vẫn dựa trên
bằng chứng tồn tại thật. Việc Agent phát hiện thêm lỗi logic (nếu có) xuất hiện như
1 issue AI riêng (category=maintainability/bug/security), KHÔNG lùi lại sửa số
compliance_score đã tính — 2 con số phục vụ 2 mục đích khác nhau (existence vs quality).

Priority Override (lý do rule engine đứng ưu tiên cao nhất):
  - Bất kỳ rule P0 nào FAIL (không phải provisional_pass) → severity = "critical",
    TỰ ĐỘNG đứng đầu generate_final_report.top_priorities, xếp TRƯỚC mọi issue AI
    tự tìm (kể cả issue AI gắn severity critical), vì đây là thiếu sót có bằng chứng
    tuyệt đối (confidence 1.0) — không thể tranh cãi như 1 nhận định của AI.
  - AI Agent KHÔNG được tự ý hạ severity hay xoá issue loại "requirement".
    Đây là điểm khác biệt với issue "ai_review"/"static": Anti-hallucination
    layer 4 (Static Cross-Check, mục 6.5) áp dụng cho AI/static, KHÔNG áp
    dụng ngược lại cho roadmap_rule.
```

### 3.6 Tích hợp vào Pipeline & Output

```text
Khi nào chạy:  Bước 3 trong Pipeline 13 bước (mục 7) — ngay sau DETECT STRUCTURE,
               TRƯỚC static analysis. Chỉ chạy nếu review_jobs.options.rule_profile != null.

Lưu ở đâu:     MongoDB collection mới `roadmap_compliance_results`
               { job_id, rule_profile, results: [RuleResult...], verification_queue: [...],
                 compliance_score, bonus_score }

Đưa vào report: review_issues — mỗi rule FAIL (không phải provisional_pass) tạo 1 row:
               category = "requirement"  (giá trị enum MỚI — xem mục 10.1)
               source   = "roadmap_rule" (giá trị enum MỚI — xem mục 10.1)
               confidence = 1.0
               raw_output = { "rule_id": "RC-W5-01", "week": 5, "skill_group": "Realtime Communication" }

               review_reports thêm 2 cột MỚI:
               compliance_score  FLOAT  -- % rule P0/P1 pass (kể cả provisional_pass), null nếu rule_profile = null
               bonus_score       FLOAT  -- % rule P2 pass, null nếu rule_profile = null

Agent biết gì: Roadmap compliance KHÔNG phải AI tool — kết quả được inject vào
               context của Agent giống static_analysis_summary (mục 4.1, Phase 2),
               dạng text: "Roadmap compliance: 71/79 PASS/PROVISIONAL. Thiếu (P0):
               RC-W5-01 (không tìm thấy WebSocket/SSE)...". Agent dùng thông tin này để
               viết executive_summary mạch lạc hơn, KHÔNG được tạo lại issue trùng qua
               generate_issue cho rule đã FAIL (tránh duplicate) — issue "requirement"
               do rule engine tự ghi thẳng vào review_issues, không qua Agent.

               Riêng verification_queue (mục 3.2b) BẮT BUỘC được merge vào danh sách
               "files ưu tiên" (Bước 6, mục 7) kèm ai_hint — Agent phải đọc các file này
               và tự quyết pass/fail thực chất, ghi issue AI riêng nếu phát hiện sai.
```

---

## 4. AI AGENT — REACT TOOL CALLING

### 4.1 Agent Pattern: ReAct (Reasoning + Acting) — qua LangChain

```text
Agent KHÔNG phải:
  - Gọi LLM 1 lần, lấy text, parse
  - Pipeline cứng: step1 → step2 → step3

Agent LÀ:
  - LangChain AgentExecutor chạy ReAct loop theo 2 phase rõ ràng
  - LLM nhìn danh sách tools (declare bằng @tool decorator) + context
  - LLM tự quyết định gọi tool nào, truyền input gì
  - Nhận output → reasoning → quyết định bước tiếp
  - Review phase loop cho đến khi đọc đủ chunk_review_plan target chunks và trả handoff
  - Backend kiểm tra coverage xong mới mở report phase để gọi generate_final_report

Triển khai: create_react_agent() + AgentExecutor từ langchain.agents
```

### 4.2 Tools Theo Phase

| # | Tool Name | Mục đích | Input chính | Output chính |
|---|---|---|---|---|
| 1 | `analyze_project_structure` | Lấy tổng quan project, danh sách file cần review | `job_id` | languages, frameworks, `files_to_review[]`, static_analysis_summary |
| 2 | `read_file_chunk` | Đọc code chunk cụ thể | `job_id, file_path, chunk_index` | content, line_start/end, `static_issues_in_range[]` |
| 3 | `search_coding_standard` | RAG lookup: tìm standard/guideline liên quan | `query, language?, top_k=3` | `results[]` (source, content, similarity_score) |
| 4 | `generate_issue` | Tạo structured issue | file_path, severity, category, title, description, confidence | Issue object (validate trước khi save) |
| 5 | `generate_final_report` | Tổng hợp report + scores | `job_id, scores, executive_summary` | scores, executive_summary, top_priorities |

**Review phase chỉ expose tool 1-4. Report phase chỉ expose tool 5.** Như vậy
agent không thể gọi `generate_final_report` giữa chừng; backend chỉ mở report phase
sau khi target chunk coverage đã đủ.

### 4.3 Agent Loop Flow

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
│  Step 5: HANDOFF (sau khi review xong)                   │
│    → trả summary: risks, static findings, roadmap notes  │
│                                                          │
│  Backend gate                                            │
│    → verify target chunks read đủ                        │
│    → nếu đủ mới chạy report phase                         │
│                                                          │
│  Report phase                                            │
│    → expose duy nhất generate_final_report               │
│                                                          │
│  EXIT CONDITIONS:                                        │
│    (a) review handoff đã có và coverage đủ               │
│    (b) Hard iteration limit                              │
│    (c) Lỗi không recover → set job FAILED                │
└──────────────────────────────────────────────────────────┘
```

### 4.4 RAG Trigger Rules — Khi nào agent PHẢI gọi RAG

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

### 4.5 LLM Client — LangChain

```text
Primary:     Google Gemini 2.0 Flash (free tier, tool calling support)
Fallback:    OpenAI gpt-4o-mini ($0.15/1M input tokens)
Client:      LangChain — ChatGoogleGenerativeAI (primary), ChatOpenAI (fallback)
Retry:       Exponential backoff (max 3 retries, base 2s) — qua LangChain retry config
Config:      API key từ .env qua core/config.py
Abstraction: LangChain BaseChatModel interface — không cần viết LLMClient abstract class
```

**Quyết định: Dùng LangChain.**
Lý do:
- Phù hợp lộ trình đào tạo (Tuần 6–7 đã học LangChain)
- Abstraction tốt cho multi-LLM: chuyển Gemini ↔ OpenAI chỉ đổi 1 dòng
- Built-in ReAct agent (`create_react_agent` + `AgentExecutor`) — không cần viết loop thủ công
- `@tool` decorator tự sinh JSON schema từ type hints + docstring
- Callback system giúp log tool calls dễ dàng (thay vì viết wrapper thủ công)

---

## 5. STATIC ANALYSIS GROUNDING

### 5.1 Flow kết hợp Static + AI

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

### 5.2 Static Tools — Cố định

| Tool | Ngôn ngữ | Command | Mục đích |
|---|---|---|---|
| `ruff` | Python | `ruff check --output-format=json` | Linting, style, basic bugs |
| `bandit` | Python | `bandit -r . -f json` | Security scan |
| `eslint` | JS/TS | `eslint --format json` | Linting + security rules |

**KHÔNG dùng:** pylint (chậm), semgrep (nặng setup — để optional advanced).

---

## 6. ANTI-HALLUCINATION — 7 LỚP BẢO VỆ

Đây là phần mentor hỏi kỹ nhất. Không thỏa hiệp.

| # | Kỹ thuật | Cách làm | Enforce ở đâu |
|---|---|---|---|
| 1 | **RAG Grounding** | Security issue PHẢI có RAG reference | System prompt + validate trong generate_issue |
| 2 | **Confidence Threshold** | Chỉ lưu issue có `confidence >= 0.7` | Validate trong `generate_issue.py` trước khi ghi DB |
| 3 | **Structured Output** | AI PHẢI dùng `generate_issue` tool, không free text | Tool calling enforcement, reject non-tool response |
| 4 | **Static Cross-Check** | AI flag + static tool confirm → tăng confidence | Logic trong report_service.py khi merge |
| 5 | **Line Validation** | line_start/line_end PHẢI tồn tại trong file thật | Backend validate trước insert review_issues |
| 6 | **Hard Limit** | Tối đa 20 tool calls/session | Enforce trong agent loop code, KHÔNG phải prompt |
| 7 | **Roadmap Priority Override** | Issue `category="requirement"` (mục 3) do rule engine ghi thẳng, KHÔNG qua Agent, KHÔNG thể bị Agent hạ severity hay xoá | `RoadmapComplianceChecker`, chạy trước Agent, ghi thẳng DB |

---

## 7. REVIEW PIPELINE END-TO-END — 13 BƯỚC

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
 │  3. ROADMAP COMPLIANCE CHECK (chỉ nếu rule_profile != null) │
 │     RoadmapComplianceChecker chạy 79 rules (mục 3.4)         │
 │     → mỗi rule FAIL ghi thẳng review_issues                 │
 │       (category=requirement, source=roadmap_rule, conf=1.0) │
 │     → lưu MongoDB roadmap_compliance_results + tính scores  │
 │                                                             │
 │  4. RUN STATIC ANALYSIS                                     │
 │     ruff + bandit (Python), eslint (JS/TS)                  │
 │     → parse → NormalizedIssue → lưu MongoDB                 │
 │                                                             │
 │  5. CHUNK CODE                                              │
 │     Python: AST → class/function/file boundary              │
 │     Non-Python/plain text: file/range chunks                │
 │     → gắn metadata (file_path, language, module, risk_area, │
 │       chunk_type, class_name, function_name, imports, lines) │
 │     → lưu MongoDB chunk_metadata                            │
 │                                                             │
 │  6. BUILD CHUNK REVIEW PLAN                                 │
 │     smart: metadata filter + static findings + roadmap AI   │
 │     verification direct chunks + related_context chunks     │
 │     full_audit: tất cả chunks trong repository              │
 │                                                             │
 │  7. INJECT CONTEXT CHO AGENT                                │
 │     analyze_project_structure trả chunk_review_plan,        │
 │     static summary, roadmap summary, verification queue     │
 │                                                             │
 │  8. AGENT LẬP KẾ HOẠCH                                      │
 │     → gọi analyze_project_structure                         │
 │     → reasoning theo chunk_review_plan, không random        │
 │                                                             │
 │  9. AGENT REVIEW LOOP (cho mỗi target chunk)                │
 │     → read_file_chunk (code + metadata + static issues)     │
 │     → dùng context_hints parent/neighbor khi cần            │
 │     → search_coding_standard (nếu nghi security issue)      │
 │     → generate_issue (confidence >= 0.7)                    │
 │     → verify roadmap_verifications theo rule_id/ai_hint     │
 │                                                             │
 │  10. VALIDATE ISSUES                                        │
 │     → line_start/line_end có tồn tại trong file?            │
 │     → confidence >= 0.7?                                    │
 │     → security issue có RAG reference?                      │
 │     → drop/flag nếu không pass                              │
 │                                                             │
 │  11. DEDUPLICATE                                            │
 │     merge roadmap_rule + static issues + AI issues          │
 │     dedup theo (file_path, line_start, category)            │
 │     giữ rõ source: 'roadmap_rule' | 'ruff' | 'bandit' |     │
 │                     'eslint' | 'ai_review'                  │
 │     roadmap_rule KHÔNG bị dedup/hạ severity bởi issue khác  │
 │                                                             │
 │  12. GENERATE REPORT + SCORES                               │
 │     → gọi generate_final_report                             │
 │     → tính security_score, maintainability_score,           │
 │       performance_score, overall_score                      │
 │     → tính compliance_score, bonus_score (nếu có rule_profile)│
 │     → viết executive_summary (ưu tiên nêu rule P0 thiếu)    │
 │     → lưu PostgreSQL review_reports + review_issues         │
 │                                                             │
 │  13. LƯU EVIDENCE + CLEANUP                                │
 │     → tool_call_logs → MongoDB (cho /ai-debug page)         │
 │     → cleanup sandbox (rm /sandbox/{job_id}/)               │
 │     → publish COMPLETED event → Redis → SSE → Frontend      │
 └─────────────────────────────────────────────────────────────┘
```

---

## 8. RAG DESIGN — CẤU TRÚC MODULE

### 8.1 File Structure

```text
backend/app/ai/rag/
├── vectorstore.py      # ChromaDB client wrapper (singleton)
├── ingestion.py        # RAGIngestionPipeline
├── retriever.py        # HybridRetriever (vector + BM25)
└── bm25_index.py       # BM25 index builder + searcher
```

### 8.2 RAG Method Selection (ghi trong báo cáo/SRS)

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

### 8.3 HybridRetriever — Pseudocode

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

### 8.4 Ingestion Pipeline

```text
1. Load document (markdown/text)
2. Split: 512 tokens, 50 token overlap
3. Attach metadata: source, chunk_index, language, doc_type, category
4. Embed → ChromaDB insert
5. Tokenize → BM25 index insert
6. Script: scripts/seed_rag.py chạy trước demo
```

### 8.5 RAG Debug — Dữ liệu cho /ai-debug page

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

## 9. SYSTEM PROMPT — BẢN CUỐI

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
  not just file-selection hints. Direct roadmap matches and related_context chunks must be
  used together to verify correctness.

## Workflow:
1. Call analyze_project_structure to understand the project
2. Read every `required_chunk_indexes` entry in `chunk_review_plan.files` using
   read_file_chunk. This plan includes direct roadmap verification files, related
   roadmap_context chunks, static findings, high-risk metadata-filtered chunks, and
   breadth samples.
3. For security concerns, ALWAYS call search_coding_standard first
4. Generate issues using generate_issue tool (NEVER as free text)
5. After reviewing all target chunks, return a concise review handoff summary.
   Do NOT call generate_final_report during review phase.

## Output format:
Always use `generate_issue` tool for each issue. Never output issues as free text.
After reviewing all target chunks, return roadmap verification notes by rule_id,
validated static findings, main risks, and suggested scores for the report phase.
"""
```

---

## 10. OUTPUT CONTRACT — KHỚP VỚI DATABASE

> ⚠️ **Thay đổi enum (breaking change so với plan gốc):** `review_issues.category` và
> `review_issues.source` phải thêm giá trị mới để hỗ trợ mục 3. Cần 1 Alembic revision
> riêng cho thay đổi này — xem checklist mục 15.
>
> ```text
> category  (cũ):  security | bug | performance | maintainability | style
> category  (mới): security | bug | performance | maintainability | style | requirement
> source    (cũ):  ai_review | ruff | bandit | eslint
> source    (mới): ai_review | ruff | bandit | eslint | roadmap_rule
> ```

### 10.1 generate_issue → review_issues (PostgreSQL)

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

### 10.1b RoadmapComplianceChecker → review_issues (PostgreSQL, KHÔNG qua Agent)

```text
rule_id          → review_issues.raw_output.rule_id
week/skill_group → review_issues.raw_output.{week, skill_group}
requirement       → review_issues.title (nguyên văn cột "Yêu cầu" ở mục 3.4)
rationale_if_missing → review_issues.description
file_path        → null (issue ở cấp repo, không gắn 1 file cụ thể)
severity         → review_issues.severity      (critical nếu P0, high nếu P1, low nếu P2)
category         → 'requirement' (hardcoded)
source           → 'roadmap_rule' (hardcoded)
confidence       → 1.0 (hardcoded)
```

### 10.2 generate_final_report → review_reports (PostgreSQL)

```text
security_score          → review_reports.security_score       (0.0 - 10.0)
maintainability_score   → review_reports.maintainability_score
performance_score       → review_reports.performance_score
overall_score            → review_reports.overall_score
executive_summary       → review_reports.executive_summary
top_priorities          → review_reports.top_risky_files (JSONB)
tech_stack              → review_reports.tech_stack (JSONB)
```

### 10.2b RoadmapComplianceChecker → review_reports (cột MỚI, chỉ set nếu rule_profile != null)

```text
compliance_score  → review_reports.compliance_score  FLOAT  -- % rule P0+P1 PASS
bonus_score       → review_reports.bonus_score        FLOAT  -- % rule P2 PASS
```

---

## 11. TOOL CALL LOGGING — CHO /AI-DEBUG PAGE

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

## 12. MÔ TẢ CHO BÁO CÁO / SLIDE

> RepoGuard AI sử dụng phương pháp **Evidence-Grounded Agentic Hybrid RAG** cho bài toán review source code cấp repository.
>
> Khác với Naive RAG chỉ truy xuất top-k chunks bằng vector similarity, hệ thống tách riêng hai loại retrieval:
>
> **(1) Repository Context Retrieval:** Source code Python được phân tích bằng AST để chia theo class/function/file boundary; non-Python và plain text được chunk theo file/range an toàn. Mỗi chunk có metadata như file_path, language, module, chunk_type, class_name, function_name, line_start, line_end, risk_area. Khi review, agent dùng metadata filtering, roadmap direct/related_context selection, và context_hints parent/neighbor để lấy đúng đoạn code cần thiết.
>
> **(2) Knowledge Retrieval:** Coding standards, OWASP Top 10, Python/FastAPI best practices được lưu trong vector database (ChromaDB) kết hợp BM25 keyword search. Khi phát hiện nghi vấn security, agent truy xuất guideline liên quan để kiểm chứng và làm căn cứ.
>
> Ngoài ra, hệ thống kết hợp static analysis tools (Ruff, Bandit, ESLint) để ground kết quả AI review, giảm hallucination. Mỗi issue sinh ra bắt buộc có severity, category, file path, line number, confidence score >= 0.7 và evidence/reference rõ ràng.
>
> **(3) Roadmap Compliance Rule Engine (điểm khác biệt so với code review tool thông thường):** Ngoài việc tìm lỗi trong code đang có, hệ thống còn đối chiếu repo với một bộ **79 rule deterministic** (opt-in theo rule profile) để phát hiện những gì **đang thiếu** — ví dụ một bài nộp được yêu cầu triển khai WebSocket nhưng không có, hoặc thiếu Alembic migration. Đây là lớp check không dùng AI (confidence = 1.0), chạy trước cả static analysis, và luôn được ưu tiên cao nhất trong report vì là bằng chứng tuyệt đối, không phải suy luận.

---

## 13. MVP vs ADVANCED — RANH GIỚI RÕ RÀNG

### MVP (Bắt buộc trong 4 tuần)

```text
✅ AST-based code chunking (Python)
✅ Metadata cho mỗi chunk (file_path, language, function_name, line_start/end)
✅ ChromaDB vector RAG cho coding standards
✅ BM25 keyword search (rank-bm25, in-memory)
✅ HybridRetriever (vector + BM25 merge)
✅ Static analysis grounding (ruff + bandit)
✅ ReAct agent tách 2 phase: review tools (1-4) và report tool (5)
✅ Structured issue JSON qua generate_issue tool
✅ Evidence mapping (RAG reference cho security issues)
✅ Confidence threshold >= 0.7
✅ Line number validation
✅ Hard iteration limit để tránh loop vô hạn
✅ Tool call logging → MongoDB
✅ LangChain (langchain-google-genai / langchain-openai) — ReAct agent + @tool decorator
✅ Roadmap Compliance Rule Engine (mục 3) — 79 rules, opt-in qua rule_profile
✅ Cả 8 check_type (mục 3.2) — đều là file/regex/dependency check, không phức tạp
✅ compliance_score + bonus_score trong review_reports
```

### Advanced (Nếu kịp, đáng làm)

```text
🔶 Parent-child context expansion tự động
🔶 risk_area auto-classification nâng cao
🔶 RAG debug page /ai-debug
🔶 Issue deduplication thông minh (fuzzy match title)
🔶 Compare 2 lần review
🔶 Multi-language AST (JS/TS via tree-sitter)
🔶 Roadmap rule profile picker ở FE (dropdown chọn "Không dùng" / "Bootcamp v1")
🔶 UI hiển thị compliance checklist dạng ✅/❌ theo từng tuần (không chỉ list issue)
🔶 Rule set v2: check động (docker image size, git commit history)
```

### KHÔNG LÀM

```text
❌ Full Graph RAG (dependency graph)
❌ Repo-wide call graph
❌ Multi-agent architecture
❌ Semgrep integration
❌ LangGraph (multi-agent graph — overkill cho tool surface đơn giản)
❌ Fine-tuning embedding model
❌ Private repo OAuth
```

---

## 14. DEPENDENCIES MỚI CẦN THÊM

```text
# Trong requirements.txt
langchain                 # LangChain core framework
langchain-google-genai    # ChatGoogleGenerativeAI (primary LLM)
langchain-openai          # ChatOpenAI (fallback LLM)
langchain-community       # Community integrations
chromadb                  # Vector DB
sentence-transformers     # Embedding model (all-MiniLM-L6-v2)
rank-bm25                 # BM25 keyword search
pyyaml                    # Đọc roadmap_rules_v2.yaml (mục 3.4)
```

---

## 15. CHECKLIST TRƯỚC KHI CODE AI MODULE

```text
- [ ] ChromaDB PersistentClient chạy được local
- [ ] seed_rag.py ingest OWASP Top 10 thành công
- [ ] BM25 index build từ cùng documents
- [ ] HybridRetriever search trả kết quả hợp lý (test manual)
- [ ] LangChain AgentExecutor + tool calling chạy được với 1 tool đơn giản
- [ ] AST chunker parse file Python thành function/class chunks
- [ ] Metadata gắn đúng cho mỗi chunk
- [ ] Agent loop chạy được end-to-end với 1 file test
- [ ] Tool call logs ghi vào MongoDB
- [ ] generate_issue validate confidence >= 0.7
- [ ] Line validation hoạt động
- [ ] Alembic revision mới: thêm 'requirement' vào category enum,
      'roadmap_rule' vào source enum, cột compliance_score + bonus_score
- [ ] RoadmapComplianceChecker parse đủ 8 check_type (mục 3.2)
- [ ] roadmap_rules_v2.yaml load đúng 79 rules, không lỗi schema
- [ ] Test RoadmapComplianceChecker trên 1 repo test thiếu WebSocket → phải
      ra đúng 1 issue category=requirement, rule_id=RC-W5-01, severity=critical
- [ ] rule_profile=null → Roadmap Compliance Rule Engine KHÔNG chạy (regression test)
- [ ] weeks_included=[1,2] → chỉ 29 rule (Tuần 1+2+GEN) được áp dụng, KHÔNG báo thiếu
      WebSocket/RAG dù repo thực sự không có (vì chưa tới tuần đó)
- [ ] Rule needs_ai_verification=true + pattern tìm thấy → status=provisional_pass,
      KHÔNG tạo issue category=requirement ngay, file được đẩy vào verification_queue
      và xuất hiện trong danh sách file bắt buộc Agent đọc (kèm ai_hint)
```

---

*Tài liệu này là quyết định cuối cùng cho AI pipeline của RepoGuard. Code theo đúng flow này.*
