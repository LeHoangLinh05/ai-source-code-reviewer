# RepoGuard AI — WBS (Work Breakdown Structure)

> **Dự án:** RepoGuard AI — AI Source Code Reviewer  
> **Tác giả:** Nguyễn Bảo Thạch  
> **Bắt đầu:** 26/06/2026 | **Deadline:** 24/07/2026 (4 tuần)  
> **Tổng số ngày làm việc:** 28 ngày  
> **Trạng thái:** Done ✅ | In Progress 🟡 | To Do 🔲

---

## 📊 TỔNG QUAN TIMELINE

| Phase | Tên | Ngày | Thời lượng | Ưu tiên |
|-------|-----|------|------------|---------|
| **1** | Môi trường & Kiến trúc Web (Backend Python) | Ngày 1–5 (26/06–30/06) | 5 ngày | 🔴 GẤP |
| **2** | DevOps & Docker | Ngày 6–7 (01/07–02/07) | 2 ngày | 🔴 GẤP |
| **3** | Database Layer: PostgreSQL, MongoDB, Redis, Alembic | Ngày 8–11 (03/07–06/07) | 4 ngày | 🔴 GẤP |
| **4** | Frontend Development với Next.js 15 | Ngày 12–17 (07/07–12/07) | 6 ngày | 🔴 GẤP |
| **5** | Realtime Communication (SSE / WebSocket / WebRTC) | Ngày 18–21 (13/07–16/07) | 4 ngày | 🟠 Quan trọng |
| **6** | AI Pipeline (Agent, RAG, Tool Calling, Polish, Demo) | Ngày 22–28 (17/07–24/07) | 7 ngày | 🟠 Quan trọng |

---

## 📋 WBS CHI TIẾT

| Category | Task | Subtask | ET (h) | Status | Start date | End date |
|----------|------|---------|--------|--------|------------|----------|
| | | | | | | |
| **Lập kế hoạch dự án** | Phân tích yêu cầu | Xác định ý tưởng, mục tiêu, input, nghiệp vụ dự án | | Done | | |
| | WBS | Xây dựng WBS theo các module chính của hệ thống | | In Progress | | |
| **Thiết kế hệ thống và database** | Thiết kế kiến trúc | Thiết kế tổng quan System Architecture diagram và review | 4 | In Progress | 6/25/26 | |
| | | Thiết kế Review Job Workflow (flowchart pipeline) | 2 | To Do | | |
| | Thiết kế cơ sở dữ liệu | Thiết kế ERD cho PostgreSQL (Users, Repositories, ReviewJobs, Reports, Issues) | 3 | To Do | | |
| | | Thiết kế schema MongoDB (file_analysis, static_outputs, tool_call_logs, chunks) | 2 | To Do | | |
| | | Thiết kế cấu trúc Redis (cache, pub/sub, token blacklist, rate limit) | 1 | To Do | | |
| | | | | | | |
| **Khởi tạo dự án** | Monorepo Structure | Tạo cấu trúc monorepo: `backend/`, `frontend/`, `docker/`, `nginx/`, `scripts/`, `docs/` | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Init git repo + `.gitignore` (Python, Node, Docker) | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo `requirements.txt` với tất cả dependencies backend | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo `.env.example` với tất cả biến môi trường | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | FastAPI Project | Tạo `backend/app/main.py` — FastAPI app instance, CORS, lifespan | 1 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo `core/config.py` — Pydantic BaseSettings, đọc `.env` | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo `core/exceptions.py` — Custom exceptions + exception handlers | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo `core/dependencies.py` — FastAPI DI (get_db, get_redis, etc.) | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | Tạo folder structure: `routers/`, `services/`, `models/`, `schemas/` | 0.5 | To Do | 6/26/26 | 6/26/26 |
| | | | | | | |
| **User Authentication** | User Login | Thiết kế trang Login (Frontend Next.js) | 6 | To Do | 6/27/26 | 6/28/26 |
| | | Xây dựng `core/security.py` — Password hash (bcrypt) + JWT (python-jose) | 1.5 | To Do | 6/27/26 | 6/27/26 |
| | | Xây dựng `schemas/auth.py` — LoginRequest, TokenResponse, UserResponse | 0.5 | To Do | 6/27/26 | 6/27/26 |
| | | Xây dựng `services/auth_service.py` — authenticate, create_tokens | 2 | To Do | 6/27/26 | 6/28/26 |
| | | Xây dựng `services/token_service.py` — JWT create/verify, Redis blacklist | 1 | To Do | 6/28/26 | 6/28/26 |
| | | Xây dựng API Login: POST `/api/auth/login` | 1.5 | To Do | 6/28/26 | 6/28/26 |
| | | Xây dựng API Refresh Token: POST `/api/auth/refresh` | 1 | To Do | 6/28/26 | 6/28/26 |
| | | Xây dựng API Logout: POST `/api/auth/logout` | 0.5 | To Do | 6/28/26 | 6/28/26 |
| | User Registration | Thiết kế trang Registration (Frontend Next.js) | 4 | To Do | 6/29/26 | 6/30/26 |
| | | Xây dựng `schemas/auth.py` — RegisterRequest schema | 0.5 | To Do | 6/29/26 | 6/29/26 |
| | | Xây dựng API Register: POST `/api/auth/register` | 2 | To Do | 6/29/26 | 6/30/26 |
| | Auth Middleware | Xây dựng `get_current_user` dependency + RBAC decorator (user/admin) | 1 | To Do | 6/30/26 | 6/30/26 |
| | | Xây dựng API GET `/api/auth/me` — Lấy thông tin user hiện tại | 0.5 | To Do | 6/30/26 | 6/30/26 |
| | | Auth middleware frontend: redirect to `/login` nếu chưa authenticated | 0.5 | To Do | 6/30/26 | 6/30/26 |
| | | Test toàn bộ auth flow qua Swagger UI | 0.5 | To Do | 6/30/26 | 6/30/26 |
| | | | | | | |
| **DevOps & Docker** | Dockerfiles | Tạo `backend/Dockerfile` — Python 3.12 slim, install ruff + bandit | 1 | To Do | 7/1/26 | 7/1/26 |
| | | Tạo `frontend/Dockerfile` — Node 20 alpine, multi-stage build (dev + prod) | 1 | To Do | 7/1/26 | 7/1/26 |
| | | Tạo `.dockerignore` cho backend và frontend | 0.5 | To Do | 7/1/26 | 7/1/26 |
| | Docker Compose | Xây dựng `docker-compose.yml` — Services: postgres, mongodb, redis, backend, worker, frontend, nginx | 2 | To Do | 7/1/26 | 7/1/26 |
| | | Cấu hình Volume mounts cho development hot reload | 0.5 | To Do | 7/1/26 | 7/1/26 |
| | | Cấu hình Environment variables từ `.env` file | 0.5 | To Do | 7/1/26 | 7/1/26 |
| | | Cấu hình `depends_on` + healthcheck cho startup order | 0.5 | To Do | 7/1/26 | 7/1/26 |
| | Nginx | Tạo `nginx/nginx.conf` — Reverse proxy: `/api` → backend, `/` → frontend | 1 | To Do | 7/2/26 | 7/2/26 |
| | | Cấu hình SSE support: `proxy_buffering off`, `X-Accel-Buffering: no` | 0.5 | To Do | 7/2/26 | 7/2/26 |
| | | Cấu hình WebSocket: `/ws` → backend upgrade connection | 0.5 | To Do | 7/2/26 | 7/2/26 |
| | Health & Testing | Backend health endpoint: `GET /api/health` | 0.5 | To Do | 7/2/26 | 7/2/26 |
| | | Test `docker-compose up --build` — tất cả services start | 1 | To Do | 7/2/26 | 7/2/26 |
| | | Verify routing qua Nginx: API + Frontend accessible | 0.5 | To Do | 7/2/26 | 7/2/26 |
| | | | | | | |
| **Database Layer** | PostgreSQL + SQLAlchemy | Xây dựng `db/postgres.py` — SQLAlchemy 2.0 async engine + session factory | 1 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/user.py` — User model (id, email, hashed_pw, role, timestamps) | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/repository.py` — Repository model (id, user_id FK, name, url, platform) | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/review_job.py` — ReviewJob model (id, repo FK, user FK, status, branch) | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/review_report.py` — ReviewReport model (id, job FK, scores, summary) | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/review_issue.py` — ReviewIssue model (id, job FK, file, severity) | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Xây dựng `models/job_status_history.py` — JobStatusHistory model | 0.5 | To Do | 7/3/26 | 7/3/26 |
| | | Cấu hình Relationships + Foreign Keys + Indexes | 1 | To Do | 7/4/26 | 7/4/26 |
| | Alembic Migration | Alembic init + alembic.ini config | 0.5 | To Do | 7/4/26 | 7/4/26 |
| | | Alembic initial migration: tạo tất cả tables | 1 | To Do | 7/4/26 | 7/4/26 |
| | | Test `alembic upgrade head` trên PostgreSQL Docker | 0.5 | To Do | 7/4/26 | 7/4/26 |
| | | Kết nối services với PostgreSQL models (CRUD operations) | 2 | To Do | 7/4/26 | 7/4/26 |
| | MongoDB | Xây dựng `db/mongodb.py` — Motor async client setup + connection management | 1 | To Do | 7/5/26 | 7/5/26 |
| | | Collection `file_analysis_results` — insert/query project structure | 1 | To Do | 7/5/26 | 7/5/26 |
| | | Collection `raw_static_analysis_outputs` — insert/query tool outputs | 0.5 | To Do | 7/5/26 | 7/5/26 |
| | | Collection `tool_call_logs` — insert/query AI agent traces | 0.5 | To Do | 7/5/26 | 7/5/26 |
| | | Collection `chunk_metadata` — insert/query code chunks | 0.5 | To Do | 7/5/26 | 7/5/26 |
| | | Index creation cho job_id, created_at trên tất cả collections | 0.5 | To Do | 7/5/26 | 7/5/26 |
| | | Pydantic models cho MongoDB documents (validate before insert) | 1 | To Do | 7/5/26 | 7/5/26 |
| | Redis | Xây dựng `db/redis.py` — redis-py async client + connection pool | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | Token blacklist service: set token JTI + TTL | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | Cache patterns: get/set with TTL cho repo stats, job status | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | Pub/Sub setup: publish/subscribe cho `job:{id}:progress` channel | 1 | To Do | 7/6/26 | 7/6/26 |
| | | Rate limiting: increment counter per user, block khi exceed | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | Integration & Testing | FastAPI startup/shutdown events: connect/disconnect tất cả DBs | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | Dependency injection: `get_db()`, `get_mongo()`, `get_redis()` | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | `scripts/create_admin.py` — Seed admin user | 0.5 | To Do | 7/6/26 | 7/6/26 |
| | | Test full flow: Auth → Create repo → Create job → Store in DBs | 1 | To Do | 7/6/26 | 7/6/26 |
| | | | | | | |
| **Repository Management** | Create Repository | Thiết kế giao diện thêm Repository (Add Repo dialog/form) | 1.5 | To Do | 7/7/26 | 7/7/26 |
| | | Xây dựng `schemas/repository.py` — RepoCreate, RepoResponse, RepoList | 0.5 | To Do | 7/7/26 | 7/7/26 |
| | | Xây dựng API tạo repository: POST `/api/repositories` | 1 | To Do | 7/7/26 | 7/7/26 |
| | | URL validation (chỉ GitHub, GitLab) | 0.5 | To Do | 7/7/26 | 7/7/26 |
| | Repository List | Thiết kế giao diện danh sách Repository (cards/table layout) | 2 | To Do | 7/8/26 | 7/8/26 |
| | | Xây dựng API lấy danh sách repo: GET `/api/repositories` | 1 | To Do | 7/8/26 | 7/8/26 |
| | Repository Detail | Thiết kế giao diện chi tiết Repository (repo info, review history) | 2 | To Do | 7/8/26 | 7/8/26 |
| | | Xây dựng API chi tiết repo: GET `/api/repositories/{id}` | 0.5 | To Do | 7/8/26 | 7/8/26 |
| | | Xây dựng API xóa repo: DELETE `/api/repositories/{id}` + confirmation dialog | 0.5 | To Do | 7/8/26 | 7/8/26 |
| | Clone & Sandbox | Xây dựng `services/clone_service.py` — safe_clone (git clone --depth 1, size limit) | 1.5 | To Do | 7/8/26 | 7/9/26 |
| | | File filter: ignore patterns (node_modules, venv, binary, etc.) | 0.5 | To Do | 7/9/26 | 7/9/26 |
| | | Sandbox cleanup scheduler (TTL 1h, auto delete) | 0.5 | To Do | 7/9/26 | 7/9/26 |
| | | | | | | |
| **Review Jobs** | Create Review Job | Thiết kế giao diện khởi tạo Review Job (Start Review button + options) | 1.5 | To Do | 7/9/26 | 7/9/26 |
| | | Xây dựng `schemas/review_job.py` — JobCreate, JobResponse, JobStatus enum | 0.5 | To Do | 7/9/26 | 7/9/26 |
| | | Xây dựng API tạo review job: POST `/api/review-jobs` | 1 | To Do | 7/9/26 | 7/9/26 |
| | | Job status enum: PENDING → CLONING → ANALYZING → STATIC → CHUNKING → AI_REVIEWING → REPORT → COMPLETED/FAILED | 0.5 | To Do | 7/9/26 | 7/9/26 |
| | Review Job List | Thiết kế giao diện danh sách Review Jobs (status badges, timestamps) | 2 | To Do | 7/9/26 | 7/10/26 |
| | | Xây dựng API lấy danh sách jobs: GET `/api/review-jobs` | 1 | To Do | 7/10/26 | 7/10/26 |
| | Review Job Detail | Thiết kế giao diện chi tiết Job + progress steps | 2 | To Do | 7/10/26 | 7/10/26 |
| | | Xây dựng `components/review/JobProgress.tsx` — Step indicators with animation | 1.5 | To Do | 7/10/26 | 7/10/26 |
| | | Xây dựng API chi tiết job: GET `/api/review-jobs/{id}` | 0.5 | To Do | 7/10/26 | 7/10/26 |
| | | Xây dựng API hủy job: DELETE `/api/review-jobs/{id}` | 0.5 | To Do | 7/10/26 | 7/10/26 |
| | Celery Worker | Xây dựng `workers/celery_app.py` — Celery instance + Redis broker config | 0.5 | To Do | 7/10/26 | 7/10/26 |
| | | Xây dựng `workers/review_worker.py` — Review task skeleton (clone → analyze → static) | 1.5 | To Do | 7/10/26 | 7/11/26 |
| | | Error handling middleware + logging setup | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | | | | | |
| **Code Analysis** | Structure Analyzer | Xây dựng `analyzers/structure_analyzer.py` — File tree builder (os.walk + filter) | 1 | To Do | 7/11/26 | 7/11/26 |
| | | Language detection (extension mapping + import analysis) | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | Framework detection (requirements.txt, package.json parsing) | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | File priority ranking (auth, api, db files = high priority) | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | Static Analysis | Xây dựng `analyzers/static_analysis/base.py` — Abstract base + NormalizedIssue model | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | Xây dựng `analyzers/static_analysis/ruff_analyzer.py` — Run ruff + parse JSON output | 1 | To Do | 7/11/26 | 7/12/26 |
| | | Xây dựng `analyzers/static_analysis/bandit_analyzer.py` — Run bandit + parse JSON | 1 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng `analyzers/static_analysis/eslint_analyzer.py` — Run eslint + parse JSON | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng `analyzers/secret_scanner.py` — Regex-based secret detection | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | Code Chunker | Xây dựng `analyzers/code_chunker.py` — Python AST-based chunking (class/function) | 2 | To Do | 7/12/26 | 7/12/26 |
| | | Fallback chunking: fixed 60 lines với 10 lines overlap | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Token counting (tiktoken hoặc estimate) | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Store chunk metadata vào MongoDB `chunk_metadata` | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | | | | | |
| **Report Management** | Report Dashboard | Thiết kế giao diện Report dashboard layout | 1 | To Do | 7/10/26 | 7/10/26 |
| | | Xây dựng `components/charts/ScoreGauge.tsx` — RadialBarChart (overall, security, maint, perf) | 1.5 | To Do | 7/10/26 | 7/11/26 |
| | | Xây dựng `components/charts/IssueBySeverityChart.tsx` — PieChart | 1 | To Do | 7/11/26 | 7/11/26 |
| | | Xây dựng `components/charts/IssuesByCategoryChart.tsx` — BarChart | 1 | To Do | 7/11/26 | 7/11/26 |
| | | Top risky files list component | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | Executive summary display (AI generated text) | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | Report API | Xây dựng `schemas/report.py` — ReportResponse, IssueResponse, IssueFilter | 0.5 | To Do | 7/11/26 | 7/11/26 |
| | | Xây dựng API lấy report: GET `/api/reports/{job_id}` | 1 | To Do | 7/11/26 | 7/11/26 |
| | | Xây dựng API lấy danh sách issues: GET `/api/reports/{job_id}/issues` + filter + pagination | 1 | To Do | 7/11/26 | 7/11/26 |
| | Issue Table & Detail | Thiết kế giao diện Issue table page layout | 1 | To Do | 7/11/26 | 7/12/26 |
| | | Xây dựng `components/review/IssueTable.tsx` — Table + filter dropdowns + pagination | 2 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng `components/review/IssueDetailDrawer.tsx` — Drawer with issue info + code snippet | 1.5 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng `components/review/CodeSnippetViewer.tsx` — Prism.js syntax highlighting | 1.5 | To Do | 7/12/26 | 7/12/26 |
| | | Severity badge component (color-coded: critical=red, high=orange, etc.) | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | Export Report | Export report: Markdown format download | 1.5 | To Do | | |
| | | Export report: PDF (WeasyPrint) — Optional | 2 | To Do | | |
| | | | | | | |
| **Dashboard & Analytics** | Dashboard UI | Thiết kế bố cục Dashboard tổng quan (layout sidebar + navbar + main content) | 2 | To Do | 7/7/26 | 7/7/26 |
| | | Sidebar navigation: Dashboard, Repositories, Reviews, Settings, (Admin) | 1 | To Do | 7/7/26 | 7/7/26 |
| | | Navbar: user avatar, notification bell, dark mode toggle | 1 | To Do | 7/7/26 | 7/7/26 |
| | Dashboard Stats | Thiết kế Stats cards (total repos, total jobs, total issues) | 2 | To Do | 7/7/26 | 7/7/26 |
| | | Xây dựng API thống kê tổng quan: GET `/api/dashboard/stats` | 1 | To Do | 7/7/26 | 7/8/26 |
| | | Recent jobs list component | 1 | To Do | 7/8/26 | 7/8/26 |
| | | Quick action buttons (Add repo, Start review) | 0.5 | To Do | 7/8/26 | 7/8/26 |
| | Dark Mode | Dark mode implementation (TailwindCSS dark: prefix) | 1 | To Do | 7/8/26 | 7/8/26 |
| | | | | | | |
| **Realtime Communication** | Backend SSE | Xây dựng SSE endpoint: GET `/api/review-jobs/{id}/stream` | 2 | To Do | 7/13/26 | 7/13/26 |
| | | Redis pub/sub → SSE bridge: subscribe `job:{id}:progress`, yield events | 2 | To Do | 7/13/26 | 7/13/26 |
| | | Event format: `status_change`, `progress_update`, `log`, `completed`, `failed` | 1 | To Do | 7/13/26 | 7/13/26 |
| | | Auto-disconnect khi job COMPLETED/FAILED | 0.5 | To Do | 7/13/26 | 7/13/26 |
| | Worker Progress | Update `review_worker.py` — Publish progress events qua Redis tại mỗi step | 2 | To Do | 7/14/26 | 7/14/26 |
| | | Progress percentages: CLONING(10%) → ANALYZING(25%) → STATIC(40%) → CHUNKING(50%) → AI(50-85%) → REPORT(90%) → DONE(100%) | 0.5 | To Do | 7/14/26 | 7/14/26 |
| | | Job status history recording trong PostgreSQL | 0.5 | To Do | 7/14/26 | 7/14/26 |
| | Frontend SSE Consumer | Xây dựng `hooks/useJobProgress.ts` — EventSource connection, parse SSE events | 2 | To Do | 7/14/26 | 7/15/26 |
| | | Auto-reconnect logic khi disconnect | 1 | To Do | 7/15/26 | 7/15/26 |
| | | Cleanup on unmount (close EventSource) | 0.5 | To Do | 7/15/26 | 7/15/26 |
| | | Update `JobProgress.tsx` — Replace API poll với SSE realtime | 1.5 | To Do | 7/15/26 | 7/15/26 |
| | | Progress animation (smooth transitions between steps) | 1 | To Do | 7/15/26 | 7/15/26 |
| | Nginx SSE Config | Update nginx.conf: `proxy_buffering off` cho SSE endpoints | 0.5 | To Do | 7/15/26 | 7/15/26 |
| | | Add `X-Accel-Buffering: no` header | 0.5 | To Do | 7/15/26 | 7/15/26 |
| | | Test SSE qua Nginx proxy | 0.5 | To Do | 7/15/26 | 7/15/26 |
| | Notifications | Job completion notification: toast/snackbar khi job done | 1 | To Do | 7/16/26 | 7/16/26 |
| | | Notification badge trên navbar (unread count) | 1 | To Do | 7/16/26 | 7/16/26 |
| | | Integration test: submit URL → SSE progress → view report (end-to-end) | 2 | To Do | 7/16/26 | 7/16/26 |
| | | Stress test: 3 concurrent jobs, verify SSE channels không conflict | 1 | To Do | 7/16/26 | 7/16/26 |
| | WebSocket (Optional) | WebSocket endpoint + connection manager | 2 | To Do | | |
| | | Bidirectional: real-time comment trên issues | 2 | To Do | | |
| | | | | | | |
| **AI Pipeline** | RAG Setup | ChromaDB setup: persistent client, collection `coding_standards` | 1 | To Do | 7/17/26 | 7/17/26 |
| | | Embedding model: `all-MiniLM-L6-v2` (sentence-transformers) setup | 1 | To Do | 7/17/26 | 7/17/26 |
| | | Xây dựng `ai/rag/ingestion.py` — Document loader + text splitter (512 tokens, 50 overlap) | 1.5 | To Do | 7/17/26 | 7/17/26 |
| | | Xây dựng `ai/rag/retriever.py` — Search similar coding standards (top-k results) | 1 | To Do | 7/17/26 | 7/18/26 |
| | | Xây dựng `scripts/seed_rag.py` — Ingest OWASP Top 10, Python best practices, Clean Code | 2 | To Do | 7/18/26 | 7/18/26 |
| | | Chuẩn bị knowledge base documents (OWASP markdown, PEP8, security checklist) | 2 | To Do | 7/18/26 | 7/18/26 |
| | | Test RAG retrieval quality: query → relevant results | 1 | To Do | 7/18/26 | 7/18/26 |
| | AI Agent Core | LLM client setup: Google Gemini 2.0 Flash (hoặc GPT-4o-mini) | 1 | To Do | 7/19/26 | 7/19/26 |
| | | Xây dựng `ai/prompts.py` — System prompt: review rules, confidence threshold, output format | 1 | To Do | 7/19/26 | 7/19/26 |
| | | Xây dựng Tool: `ai/tools/read_file.py` — read_file_chunk (input: job_id, file, chunk_idx) | 1 | To Do | 7/19/26 | 7/19/26 |
| | | Xây dựng Tool: `ai/tools/search_rag.py` — search_coding_standard (input: query, language) | 1 | To Do | 7/19/26 | 7/19/26 |
| | | Xây dựng Tool: `ai/tools/generate_issue.py` — generate_issue (structured issue output) | 1 | To Do | 7/19/26 | 7/20/26 |
| | | Xây dựng Tool: `ai/tools/generate_report.py` — generate_final_report (scores + summary) | 1 | To Do | 7/20/26 | 7/20/26 |
| | | Xây dựng `ai/agent.py` — Main AI Agent: ReAct loop, tool calling, max 20 calls/session | 3 | To Do | 7/20/26 | 7/21/26 |
| | | Tool call logger → MongoDB `tool_call_logs` (mỗi call: input, output, duration) | 1 | To Do | 7/21/26 | 7/21/26 |
| | Issue Processing | Issue deduplication: merge static analysis + AI issues (by file_path + line_start) | 1 | To Do | 7/21/26 | 7/21/26 |
| | | Report score calculation (security, maintainability, performance, overall) | 1 | To Do | 7/21/26 | 7/21/26 |
| | | Anti-hallucination: confidence ≥ 0.7 filter, line number validation | 0.5 | To Do | 7/21/26 | 7/21/26 |
| | | Save review_reports + review_issues vào PostgreSQL | 1 | To Do | 7/21/26 | 7/21/26 |
| | | Retry logic: exponential backoff cho LLM API calls | 0.5 | To Do | 7/21/26 | 7/21/26 |
| | Worker Integration | Update `review_worker.py` — Full pipeline: clone → analyze → static → chunk → AI → report | 2 | To Do | 7/21/26 | 7/22/26 |
| | | End-to-end test với 1 small test repo | 1 | To Do | 7/22/26 | 7/22/26 |
| | | End-to-end test với 1 medium test repo | 1 | To Do | 7/22/26 | 7/22/26 |
| | | | | | | |
| **AI Debug & Admin** | AI Debug Page | Backend: GET `/api/admin/jobs/{id}/traces` — Return tool_call_logs từ MongoDB | 1 | To Do | 7/22/26 | 7/22/26 |
| | | Thiết kế giao diện AI Debug page: select job → view traces | 1 | To Do | 7/22/26 | 7/22/26 |
| | | Xây dựng `components/debug/ToolCallTimeline.tsx` — Timeline viewer: tool calls in order | 2 | To Do | 7/22/26 | 7/23/26 |
| | | Xây dựng `components/debug/RAGChunkViewer.tsx` — Show query → retrieved docs + similarity scores | 1.5 | To Do | 7/23/26 | 7/23/26 |
| | | Expandable tool call details: input JSON + output JSON | 1 | To Do | 7/23/26 | 7/23/26 |
| | Admin Page | Thiết kế giao diện Admin: xem tất cả user jobs, system stats | 2 | To Do | 7/23/26 | 7/23/26 |
| | | Xây dựng API admin: GET `/api/admin/jobs`, GET `/api/admin/stats` | 1 | To Do | 7/23/26 | 7/23/26 |
| | | Rate limiting per user (Redis counter) | 0.5 | To Do | 7/23/26 | 7/23/26 |
| | | | | | | |
| **User Management** | User Profile | Thiết kế giao diện Settings/Profile page | 1 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng API lấy thông tin profile: GET `/api/users/me` | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng API cập nhật profile: PUT `/api/users/me` | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Xây dựng API đổi mật khẩu: POST `/api/users/me/change-password` | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | | | | | |
| **UI Polish & UX** | Loading States | Skeleton components cho tables, cards, charts | 1 | To Do | 7/12/26 | 7/12/26 |
| | Empty States | "No repositories", "No reviews", "No issues" UI | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | Error Handling | Generic error boundary UI | 0.5 | To Do | 7/12/26 | 7/12/26 |
| | | Error handling audit: tất cả API errors có UI message rõ ràng | 1 | To Do | 7/23/26 | 7/23/26 |
| | Responsive Design | Responsive design check (mobile, tablet) | 1 | To Do | 7/12/26 | 7/12/26 |
| | | | | | | |
| **Final Demo & Documentation** | Production Build | Docker Compose production build test | 1 | To Do | 7/24/26 | 7/24/26 |
| | End-to-End Testing | End-to-end flow test: nhập URL → progress → report → issues → AI debug | 2 | To Do | 7/24/26 | 7/24/26 |
| | | Fix critical bugs found during testing | 2 | To Do | 7/24/26 | 7/24/26 |
| | | `scripts/test_review.sh` — Demo script (seed data, run review) | 1 | To Do | 7/24/26 | 7/24/26 |
| | Documentation | README.md: project description, architecture, setup guide, screenshots | 1.5 | To Do | 7/24/26 | 7/24/26 |
| | | `docs/architecture.md` — System architecture documentation | 0.5 | To Do | 7/24/26 | 7/24/26 |
| | | `docs/api.md` — API documentation overview | 0.5 | To Do | 7/24/26 | 7/24/26 |
| | Demo Prep | Chuẩn bị demo: 2 sample repos ready, talking points | 1 | To Do | 7/24/26 | 7/24/26 |

---

## 📋 TỔNG HỢP SỐ LIỆU

| Metric | Giá trị |
|--------|---------|
| **Tổng số tasks** | ~135 tasks |
| **Lập kế hoạch & Thiết kế** | ~7 tasks |
| **Khởi tạo dự án** | ~9 tasks |
| **User Authentication** | ~14 tasks |
| **DevOps & Docker** | ~13 tasks |
| **Database Layer** | ~23 tasks |
| **Repository Management** | ~11 tasks |
| **Review Jobs** | ~12 tasks |
| **Code Analysis** | ~12 tasks |
| **Report Management** | ~14 tasks |
| **Dashboard & Analytics** | ~8 tasks |
| **Realtime Communication** | ~17 tasks |
| **AI Pipeline** | ~23 tasks |
| **AI Debug & Admin** | ~8 tasks |
| **User Management** | ~4 tasks |
| **UI Polish & UX** | ~5 tasks |
| **Final Demo & Documentation** | ~7 tasks |
| **Optional tasks** | 4 tasks (WebSocket, PDF export) |

---

## ⚡ MẸO QUẢN LÝ TIẾN ĐỘ

1. **Nguyên tắc 80/20:** Ưu tiên 20% tasks tạo ra 80% giá trị demo. Core flow: Login → Add repo → Review → Report PHẢI hoạt động.
2. **Phase 1–4 làm GẤP:** Bạn đã có kiến thức lý thuyết → focus vào code, không cần nghiên cứu thêm.
3. **Không perfectionism:** UI đẹp 70% là đủ. Debug page basic là OK. Export PDF bỏ nếu không kịp.
4. **Test sớm, test thường xuyên:** Sau mỗi phase, test end-to-end flow. Đừng để tới Phase 6 mới phát hiện Phase 1 bị lỗi.
5. **Docker first:** Phase 2 đặt sớm để tất cả dev đều chạy trong Docker — tránh "works on my machine".

---

*Cập nhật lần cuối: 28/06/2026*
