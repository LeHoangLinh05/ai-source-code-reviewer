# TASK.md — RepoGuard AI

> Checklist theo 6 phase, convert từ WBS trong `RepoGuard_AI_Project_Plan.md`.
> Mỗi task có ID riêng (vd `P1.3`) để tham chiếu nhanh: "làm task P1.3" hoặc "fix P6.5".
> Bắt đầu: 26/06/2026 · Deadline: 28 ngày · Hôm nay: 02/07/2026 (Ngày 7 — đang chốt Docker/DB vertical slice).
> Note 2026-07-01: Từ thời điểm này chuyển sang làm theo vertical slice (API + FE cho từng module) để luôn có flow E2E sau mỗi bước; checklist phase gốc vẫn giữ để tracking scope, không có nghĩa là bỏ sót phase.
> Note 2026-07-02: Đồng bộ lại theo `RepoGuard_AI_Project_Plan.md` + `AI_flow.md` bản mới: thêm Roadmap Compliance Rule Engine, Evidence-Grounded Agentic Hybrid RAG, 13-step review pipeline, `rule_profile/weeks_included`, `verification_queue`, `compliance_score` và `bonus_score`.

---

## PHASE 1 — Backend Core (Ngày 1–5)

- [x] **P1.1** Khởi tạo monorepo: `backend/`, `frontend/`, `docker/`, `nginx/`, `scripts/`
- [x] **P1.2** FastAPI project structure (routers, services, models, schemas, core)
- [x] **P1.3** `core/config.py` — Pydantic Settings đọc `.env`
- [x] **P1.4** Auth: register, login (JWT access + refresh)
- [x] **P1.5** Auth: logout (blacklist token qua Redis)
- [x] **P1.6** RBAC: role `user` / `admin` + `get_current_user` dependency
- [x] **P1.7** CRUD module `repositories` (thêm/xóa/xem repo URL)
- [x] **P1.8** Module `review_jobs` (tạo job, lấy status, cancel)
- [x] **P1.9** Module `reports` (query report, issues list filter/pagination)
- [x] **P1.10** Clone service: `git clone --depth 1` + size validation
- [x] **P1.11** File filter: bỏ qua binary, node_modules, venv, `__pycache__`
- [x] **P1.12** Sandbox cleanup scheduler (TTL 1h)
- [x] **P1.13** Structure analyzer: language/framework detect, file tree builder
- [x] **P1.14** Static analysis integration: Ruff + Bandit + ESLint parsers
- [x] **P1.15** `NormalizedIssue` model
- [x] **P1.16** Secret scanner (regex-based)
- [x] **P1.17** Celery setup (Redis broker) + worker skeleton
- [x] **P1.18** Error handling middleware + custom exceptions

**Done khi:**
- [x] `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` hoạt động
- [x] JWT + RBAC đúng
- [x] CRUD `/repositories` hoạt động
- [x] `POST /review-jobs` tạo job + enqueue Celery
- [x] Worker clone repo, chạy structure + static analysis
- [x] Ruff/Bandit parse thành `NormalizedIssue`
- [x] `/docs` (Swagger) hoạt động

---

## PHASE 2 — DevOps & Docker (Ngày 6–7)

- [x] **P2.1** Dockerfile backend (Python 3.12 slim + ruff, bandit)
- [x] **P2.2** Dockerfile frontend (Node 20 alpine, multi-stage)
- [x] **P2.3** `docker-compose.yml` dev: postgres, mongodb, redis, backend, worker, frontend, nginx
- [x] **P2.4** Nginx config: `/api` → backend, `/` → frontend, `/ws` → backend
- [x] **P2.5** Nginx: `proxy_buffering off` cho SSE
- [x] **P2.6** `.env.example` đầy đủ biến môi trường
- [x] **P2.7** Health check endpoint `/api/health`
- [x] **P2.8** Volume mounts cho hot reload dev

**Done khi:**
- [x] `docker-compose up --build` không lỗi
- [x] Backend qua Nginx `http://localhost:8080/api/docs` hoạt động
- [x] Frontend qua Nginx `http://localhost:8080` hoạt động
- [x] Hot reload hoạt động

---

## PHASE 3 — Database Layer (Ngày 8–11)

**PostgreSQL**
- [x] **P3.1** SQLAlchemy 2.0 async engine + session factory
- [x] **P3.2** Models: User, Repository, ReviewJob, ReviewReport, ReviewIssue, JobStatusHistory
- [x] **P3.3** Relationships + FK + indexes
- [x] **P3.4** Alembic setup + initial migration

**MongoDB**
- [x] **P3.5** Motor (async PyMongo) client setup
- [x] **P3.6** Collections: `file_analysis_results`, `raw_static_analysis_outputs`, `tool_call_logs`, `chunk_metadata`, `roadmap_compliance_results`
- [x] **P3.7** Index cho field hay query
- [x] **P3.8** CRUD helpers theo collection

**Redis**
- [x] **P3.9** Redis async client setup
- [x] **P3.10** Token blacklist service (set + TTL)
- [x] **P3.11** Pub/Sub cho job progress
- [x] **P3.12** Rate limiting per user (counter + TTL)

**Kết nối tổng**
- [x] **P3.13** DI cho DB trong FastAPI + connection pooling
- [x] **P3.14** Startup/shutdown events cho mọi DB connection
- [x] **P3.15** `scripts/create_admin.py`
- [x] **P3.16** Roadmap contract migration: `review_issues.category=requirement`, `review_issues.source=roadmap_rule`, `review_reports.compliance_score`, `review_reports.bonus_score`

**Done khi:**
- [x] `alembic upgrade head` tạo đủ table
- [ ] CRUD trên mọi PostgreSQL model
- [x] MongoDB insert/query `file_analysis_results`, `tool_call_logs`
- [x] MongoDB schema/index cho `roadmap_compliance_results`
- [x] Redis blacklist/cache/pub-sub hoạt động
- [x] Celery worker nhận task từ Redis queue
- [x] Rate limiting block user khi vượt limit
- [ ] Data persist khi restart container

---

## PHASE 4 — Frontend Next.js 15 (Ngày 12–17)

- [x] **P4.1** Setup Next.js 15 App Router + Tailwind + shadcn/ui
- [x] **P4.2** Axios instance + interceptor auto-refresh token
- [x] **P4.3** State management cho `auth`, `job`, `filter` stores — implement bằng Redux Toolkit (`authSlice`, `jobSlice`, `filterSlice`) + typed hooks
- [ ] **P4.4** TypeScript types cho mọi API response _(Slice 1: repository API types done; Slice 2: review job API types done; Slice 3: report/issue API types done)_
- [x] **P4.5** `/login`, `/register` + protected route middleware
- [ ] **P4.6** Dashboard layout: sidebar + navbar responsive
- [ ] **P4.7** `/dashboard` — overview, stats cards
- [x] **P4.8** `/repositories` + `/repositories/[id]`
- [x] **P4.9** `/reviews` job list với status badge
- [x] **P4.10** `/reviews/[id]` job detail (poll status, chưa cần SSE thật)
- [x] **P4.11** `/reviews/[id]/report` — score gauges, pie/bar chart, top risky files
- [x] **P4.12** `/reviews/[id]/issues` — table filter/pagination/search
- [x] **P4.13** Issue detail drawer: Prism.js code viewer + AI suggestion
- [ ] **P4.14** `/settings` — profile, đổi password
- [ ] **P4.15** Polish: loading/empty states, error boundary, dark mode, responsive

**Done khi:**
- [ ] Flow login → dashboard → add repo → start review → view report hoạt động
- [ ] Report dashboard render đúng data
- [ ] Issue table filter/pagination hoạt động
- [ ] Dark mode toggle hoạt động

---

## PHASE 5 — Realtime (Ngày 18–21)

- [ ] **P5.1** SSE endpoint `/review-jobs/{id}/stream`
- [ ] **P5.2** Redis pub/sub → SSE bridge (channel `job:{id}:progress`)
- [ ] **P5.3** Worker publish progress event mỗi step
- [ ] **P5.4** Event format: `status_change`, `progress_update`, `log`, `completed`, `failed`
- [ ] **P5.5** Auto-disconnect khi COMPLETED/FAILED
- [ ] **P5.6** Frontend hook `useJobProgress` (EventSource + auto-reconnect + cleanup)
- [ ] **P5.7** Progress UI: step indicator có animation
- [ ] **P5.8** Nginx SSE config (`proxy_buffering off`, `X-Accel-Buffering: no`)
- [ ] **P5.9** (Optional) WebSocket endpoint nếu kịp
- [ ] **P5.10** Notification badge trên navbar khi job complete
- [ ] **P5.11** Integration test: 3 concurrent jobs

**Done khi:**
- [ ] Progress bar update realtime theo từng step worker
- [ ] SSE qua Nginx không bị buffer
- [ ] Auto-reconnect khi disconnect
- [ ] 3 job chạy song song không conflict channel

---

## PHASE 6 — AI Pipeline: Evidence-Grounded Agentic Hybrid RAG (Ngày 22–28)

> Đọc `ai_agent.md`, `AI_flow.md`, và mục 6/13 trong `RepoGuard_AI_Project_Plan.md` trước khi làm phase này.
> Quyết định kiến trúc mới: Review Pipeline là trung tâm; RAG chỉ là một lớp grounding. Roadmap Compliance Rule Engine chạy trước static analysis và AI review, opt-in qua `review_jobs.options.rule_profile`.

**Sprint 1 — AI Core + Roadmap Compliance (Ngày 22–25)**
- [ ] **P6.1** `roadmap_rules_v2.yaml`: đủ 79 rules, schema validate, 40 P0 + 26 P1 + 13 P2, 16 rule có `needs_ai_verification=true`
- [ ] **P6.2** `RoadmapComplianceChecker`: hỗ trợ đủ 8 `check_type` (`required_file`, `required_any_of`, `required_folder`, `forbidden_tracked_file`, `required_dependency`, `required_code_pattern`, `min_file_count`, `required_config_key`)
- [ ] **P6.3** `rule_profile` contract: mặc định `null`, profile `roadmap_bootcamp_v1`, filter `weeks_included` + luôn áp dụng rule `GEN`
- [ ] **P6.4** Roadmap output: ghi MongoDB `roadmap_compliance_results` gồm `results`, `verification_queue`, `compliance_score`, `bonus_score`
- [ ] **P6.5** Roadmap issue writer: rule FAIL ghi thẳng `review_issues` với `category=requirement`, `source=roadmap_rule`, `confidence=1.0`
- [ ] **P6.6** `needs_ai_verification`: deterministic PASS → `provisional_pass`, đẩy `(rule_id, file_path, ai_hint)` vào `verification_queue`, không tạo issue requirement
- [ ] **P6.7** Priority override: P0 FAIL đứng đầu report priority, Agent không được hạ severity/xóa issue `roadmap_rule`
- [ ] **P6.8** Merge `verification_queue` vào danh sách file ưu tiên Agent đọc
- [ ] **P6.9** Code chunker AST-based cho Python theo semantic boundary class/function
- [ ] **P6.10** Chunk metadata bắt buộc: `file_path`, `language`, `module`, `risk_area`, `line_start`, `line_end`, `imports`, `function_name`
- [ ] **P6.11** ChromaDB setup + persistent storage
- [ ] **P6.12** Knowledge base ingestion: OWASP Top 10, Python best practices, Clean Code
- [ ] **P6.13** Embedding model `all-MiniLM-L6-v2` local CPU
- [ ] **P6.14** BM25 keyword search (`rank-bm25`) + HybridRetriever merge vector + keyword results
- [ ] **P6.15** Tool 1 `analyze_project_structure`
- [ ] **P6.16** Tool 2 `read_file_chunk`
- [ ] **P6.17** Tool 3 `search_coding_standard`
- [ ] **P6.18** Tool 4 `generate_issue`
- [ ] **P6.19** Tool 5 `generate_final_report`
- [ ] **P6.20** LLM client direct SDK: Gemini 2.0 Flash (`google-genai`) + fallback OpenAI `gpt-4o-mini`; không dùng LangChain/LangGraph
- [ ] **P6.21** System prompt cuối: inject static summary + roadmap compliance summary, confidence threshold, no duplicate roadmap issues
- [ ] **P6.22** Tool call logger → MongoDB `tool_call_logs`
- [ ] **P6.23** Issue deduplication: merge `roadmap_rule` + static + AI theo `(file_path, line_start, category)` nhưng không hạ `roadmap_rule`
- [ ] **P6.24** Report score calculation: security, maintainability, performance, overall, `compliance_score`, `bonus_score`
- [ ] **P6.25** Anti-hallucination 7 lớp: RAG grounding cho security issue, evidence reference, structured output, static cross-check, line validation, confidence ≥ 0.7, hard limit 20 tool calls

**Sprint 2 — Debug Page + Polish (Ngày 26–27)**
- [ ] **P6.26** `/ai-debug` — tool call timeline viewer
- [ ] **P6.27** RAG chunk viewer: query → retrieved documents + similarity scores
- [ ] **P6.28** Roadmap compliance UI: profile picker (`Không dùng` / `Bootcamp v1`) + `weeks_included`
- [ ] **P6.29** Roadmap compliance checklist UI theo tuần: PASS/FAIL/PROVISIONAL + P0/P1/P2
- [ ] **P6.30** Export report Markdown/PDF (ưu tiên Markdown, PDF nếu kịp)
- [ ] **P6.31** Admin page: xem job + system health của mọi user
- [ ] **P6.32** Error handling toàn bộ: timeout, failed job, API error
- [ ] **P6.33** Loading/empty state polish
- [ ] **P6.34** README + screenshots + demo script

**Sprint 3 — Final Demo (Ngày 28)**
- [ ] **P6.35** Docker Compose production build test
- [ ] **P6.36** E2E test theo 13-step pipeline: nhập URL → clone → structure → optional roadmap check → static analysis → chunk → Agent → report → issues → AI debug
- [ ] **P6.37** Regression test: `rule_profile=null` không chạy Roadmap Compliance và không sinh issue `category=requirement`
- [ ] **P6.38** Regression test: `weeks_included=[1,2]` chỉ áp Tuần 1 + Tuần 2 + GEN, không báo thiếu WebSocket/RAG
- [ ] **P6.39** Regression test: `needs_ai_verification=true` PASS existence → `provisional_pass` + file vào `verification_queue`
- [ ] **P6.40** Fix critical bugs
- [ ] **P6.41** Demo video/script
- [ ] **P6.42** Seed sample data cho demo

**Dependencies cần thêm trước khi code AI module:**
- [ ] `google-genai`
- [ ] `openai`
- [ ] `chromadb`
- [ ] `sentence-transformers`
- [ ] `rank-bm25`
- [ ] `pyyaml`

**Done khi:**
- [ ] Agent chạy E2E với ≥ 2 repo test
- [ ] 13-step review pipeline chạy đúng thứ tự trong `AI_flow.md` mục 7
- [ ] `tool_call_logs` trong MongoDB có đủ trace
- [ ] `review_reports` có scores, `compliance_score`, `bonus_score`
- [ ] `review_issues` chỉ chứa AI issue confidence ≥ 0.7; roadmap issue luôn confidence 1.0
- [ ] Security issue do AI tạo có RAG reference/evidence hợp lệ
- [ ] RAG retrieve đúng OWASP content khi review security-related code
- [ ] `/ai-debug` hiển thị tool calls + RAG results
- [ ] Roadmap Compliance Rule Engine chạy đúng 79/79 rules khi `rule_profile="roadmap_bootcamp_v1"`
- [ ] `weeks_included=[1,2]` chỉ áp 29 rule (Tuần 1 + Tuần 2 + GEN), không báo thiếu RAG/WebSocket
- [ ] Rule `needs_ai_verification=true` PASS existence → `provisional_pass`, file vào `verification_queue`, Agent đọc và tự quyết correctness
- [ ] Khi `rule_profile=null`, không có issue nào `category=requirement`
- [ ] Export report hoạt động (≥ Markdown)
- [ ] `docker-compose up` chạy toàn bộ hệ thống
- [ ] Demo flow E2E mượt
- [ ] README có hướng dẫn chạy + screenshot

---

## Demo Day Checklist (cuối cùng)

- [ ] `docker-compose up` → toàn bộ stack chạy
- [ ] Demo flow: nhập URL → theo dõi progress → xem report
- [ ] Roadmap Compliance demo: chọn `roadmap_bootcamp_v1`, chọn `weeks_included`, show PASS/FAIL/PROVISIONAL + `compliance_score`
- [ ] AI debug page: show tool calls + RAG retrievals
- [ ] Issue detail: click issue → code snippet + gợi ý
- [ ] Compare 2 lần review (nếu kịp)
- [ ] README rõ ràng kèm screenshot
