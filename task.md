# TASK.md — RepoGuard AI

> Checklist theo 6 phase, convert từ WBS trong `RepoGuard_AI_Project_Plan.md`.
> Mỗi task có ID riêng (vd `P1.3`) để tham chiếu nhanh: "làm task P1.3" hoặc "fix P6.5".
> Bắt đầu: 26/06/2026 · Deadline: 28 ngày · Hôm nay: 30/06/2026 (Ngày 5 — đang ở cuối Phase 1).

---

## PHASE 1 — Backend Core (Ngày 1–5)

- [x] **P1.1** Khởi tạo monorepo: `backend/`, `frontend/`, `docker/`, `nginx/`, `scripts/`
- [x] **P1.2** FastAPI project structure (routers, services, models, schemas, core)
- [x] **P1.3** `core/config.py` — Pydantic Settings đọc `.env`
- [x] **P1.4** Auth: register, login (JWT access + refresh)
- [x] **P1.5** Auth: logout (blacklist token qua Redis)
- [x] **P1.6** RBAC: role `user` / `admin` + `get_current_user` dependency
- [ ] **P1.7** CRUD module `repositories` (thêm/xóa/xem repo URL)
- [ ] **P1.8** Module `review_jobs` (tạo job, lấy status, cancel)
- [ ] **P1.9** Module `reports` (query report, issues list filter/pagination)
- [ ] **P1.10** Clone service: `git clone --depth 1` + size validation
- [ ] **P1.11** File filter: bỏ qua binary, node_modules, venv, `__pycache__`
- [ ] **P1.12** Sandbox cleanup scheduler (TTL 1h)
- [ ] **P1.13** Structure analyzer: language/framework detect, file tree builder
- [ ] **P1.14** Static analysis integration: Ruff + Bandit + ESLint parsers
- [ ] **P1.15** `NormalizedIssue` model
- [ ] **P1.16** Secret scanner (regex-based)
- [ ] **P1.17** Celery setup (Redis broker) + worker skeleton
- [ ] **P1.18** Error handling middleware + custom exceptions

**Done khi:**
- [x] `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` hoạt động
- [x] JWT + RBAC đúng
- [ ] CRUD `/repositories` hoạt động
- [ ] `POST /review-jobs` tạo job + enqueue Celery
- [ ] Worker clone repo, chạy structure + static analysis
- [ ] Ruff/Bandit parse thành `NormalizedIssue`
- [x] `/docs` (Swagger) hoạt động

---

## PHASE 2 — DevOps & Docker (Ngày 6–7)

- [ ] **P2.1** Dockerfile backend (Python 3.12 slim + ruff, bandit)
- [ ] **P2.2** Dockerfile frontend (Node 20 alpine, multi-stage)
- [ ] **P2.3** `docker-compose.yml` dev: postgres, mongodb, redis, backend, worker, frontend, nginx
- [ ] **P2.4** Nginx config: `/api` → backend, `/` → frontend, `/ws` → backend
- [ ] **P2.5** Nginx: `proxy_buffering off` cho SSE
- [ ] **P2.6** `.env.example` đầy đủ biến môi trường
- [ ] **P2.7** Health check endpoint `/api/health`
- [ ] **P2.8** Volume mounts cho hot reload dev

**Done khi:**
- [ ] `docker-compose up --build` không lỗi
- [ ] Backend qua `http://localhost/api/docs`
- [ ] Frontend qua `http://localhost`
- [ ] Hot reload hoạt động

---

## PHASE 3 — Database Layer (Ngày 8–11)

**PostgreSQL**
- [x] **P3.1** SQLAlchemy 2.0 async engine + session factory
- [x] **P3.2** Models: User, Repository, ReviewJob, ReviewReport, ReviewIssue, JobStatusHistory
- [x] **P3.3** Relationships + FK + indexes
- [x] **P3.4** Alembic setup + initial migration

**MongoDB**
- [ ] **P3.5** Motor (async PyMongo) client setup
- [ ] **P3.6** Collections: `file_analysis_results`, `raw_static_analysis_outputs`, `tool_call_logs`, `chunk_metadata`
- [ ] **P3.7** Index cho field hay query
- [ ] **P3.8** CRUD helpers theo collection

**Redis**
- [x] **P3.9** Redis async client setup
- [x] **P3.10** Token blacklist service (set + TTL)
- [ ] **P3.11** Pub/Sub cho job progress
- [ ] **P3.12** Rate limiting per user (counter + TTL)

**Kết nối tổng**
- [ ] **P3.13** DI cho DB trong FastAPI + connection pooling
- [ ] **P3.14** Startup/shutdown events cho mọi DB connection
- [ ] **P3.15** `scripts/create_admin.py`

**Done khi:**
- [ ] `alembic upgrade head` tạo đủ table
- [ ] CRUD trên mọi PostgreSQL model
- [ ] MongoDB insert/query `file_analysis_results`, `tool_call_logs`
- [ ] Redis blacklist/cache/pub-sub hoạt động
- [ ] Celery worker nhận task từ Redis queue
- [ ] Data persist khi restart container

---

## PHASE 4 — Frontend Next.js 15 (Ngày 12–17)

- [x] **P4.1** Setup Next.js 15 App Router + Tailwind + shadcn/ui
- [x] **P4.2** Axios instance + interceptor auto-refresh token
- [x] **P4.3** Redux Toolkit: `authSlice`, `jobSlice`, `filterSlice` + typed hooks
- [ ] **P4.4** TypeScript types cho mọi API response
- [x] **P4.5** `/login`, `/register` + protected route middleware
- [ ] **P4.6** Dashboard layout: sidebar + navbar responsive
- [ ] **P4.7** `/dashboard` — overview, stats cards
- [ ] **P4.8** `/repositories` + `/repositories/[id]`
- [ ] **P4.9** `/reviews` job list với status badge
- [ ] **P4.10** `/reviews/[id]` job detail (poll status, chưa cần SSE thật)
- [ ] **P4.11** `/reviews/[id]/report` — score gauges, pie/bar chart, top risky files
- [ ] **P4.12** `/reviews/[id]/issues` — table filter/pagination/search
- [ ] **P4.13** Issue detail drawer: Prism.js code viewer + AI suggestion
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

## PHASE 6 — AI Pipeline: Agent, RAG, Tool Calling (Ngày 22–28)

> Đọc `ai_agent.md` trước khi làm phase này.

**Sprint 1 — AI Core (Ngày 22–25)**
- [ ] **P6.1** Code chunker AST-based (semantic boundary)
- [ ] **P6.2** ChromaDB setup + persistent storage
- [ ] **P6.3** Ingest knowledge base: OWASP Top 10, Python best practices, Clean Code
- [ ] **P6.4** Embedding model `all-MiniLM-L6-v2` local CPU
- [ ] **P6.5** RAG retriever (`search_coding_standard`)
- [ ] **P6.6** Tool 1 `analyze_project_structure`
- [ ] **P6.7** Tool 2 `read_file_chunk`
- [ ] **P6.8** Tool 3 `search_coding_standard`
- [ ] **P6.9** Tool 4 `generate_issue`
- [ ] **P6.10** Tool 5 `generate_final_report`
- [ ] **P6.11** LLM client: Gemini 2.0 Flash (fallback GPT-4o-mini)
- [ ] **P6.12** System prompt (review rules, confidence threshold)
- [ ] **P6.13** Tool call logger → MongoDB `tool_call_logs`
- [ ] **P6.14** Issue deduplication: merge static + AI issues
- [ ] **P6.15** Report score calculation
- [ ] **P6.16** Anti-hallucination: confidence ≥ 0.7, line validation, RAG grounding
- [ ] **P6.17** Hard limit 20 tool calls/session

**Sprint 2 — Debug Page + Polish (Ngày 26–27)**
- [ ] **P6.18** `/ai-debug` — tool call timeline viewer
- [ ] **P6.19** RAG chunk viewer (query → retrieved docs + similarity score)
- [ ] **P6.20** Export report Markdown/PDF (WeasyPrint)
- [ ] **P6.21** Admin page: xem job + system health của mọi user
- [ ] **P6.22** Error handling toàn bộ: timeout, failed job, API error
- [ ] **P6.23** Loading/empty state polish
- [ ] **P6.24** Rate limiting per user
- [ ] **P6.25** README + screenshots + demo script

**Sprint 3 — Final Demo (Ngày 28)**
- [ ] **P6.26** Docker Compose production build test
- [ ] **P6.27** E2E test: nhập URL → progress → report → issues → AI debug
- [ ] **P6.28** Fix critical bug
- [ ] **P6.29** Demo video/script
- [ ] **P6.30** Seed sample data cho demo

**Done khi:**
- [ ] Agent chạy E2E với ≥ 2 repo test
- [ ] `tool_call_logs` có đủ trace
- [ ] `review_reports` có score, `review_issues` chỉ chứa confidence ≥ 0.7
- [ ] RAG retrieve đúng OWASP content khi review security code
- [ ] `/ai-debug` hiển thị tool calls + RAG result
- [ ] Export report hoạt động (≥ Markdown)
- [ ] `docker-compose up` chạy toàn bộ hệ thống
- [ ] Demo flow E2E mượt
- [ ] README có hướng dẫn chạy + screenshot

---

## Demo Day Checklist (cuối cùng)

- [ ] `docker-compose up` → toàn bộ stack chạy
- [ ] Demo flow: nhập URL → theo dõi progress → xem report
- [ ] AI debug page: show tool calls + RAG retrievals
- [ ] Issue detail: click issue → code snippet + gợi ý
- [ ] Compare 2 lần review (nếu kịp)
- [ ] README rõ ràng kèm screenshot
