# RepoGuard AI

RepoGuard AI is an AI-assisted source-code review platform. A user registers a
repository, starts a review, watches the worker pipeline in realtime, and then
opens the generated report, grouped issues, source evidence, and AI execution
trace. The current review architecture is backend-directed probe review: static
analysis and focused probes create evidence, the knowledge base grounds issue
decisions, and code embeddings are cached by repository and branch.

## Authorization model

RepoGuard has one application role: `user`. The `role` column and the `role`
field in auth responses remain for API and database compatibility, but the only
accepted value is `user`. Legacy `admin` rows are normalized by the latest
Alembic migration. There is no admin API, admin router, or admin bypass.

Every repository, review job, report, summary, AI trace, and progress stream is
owner-only. A user cannot read or mutate another user's resources, even if they
know an ID. Words such as `admin` and `role` remain in the probe knowledge base
because RepoGuard reviews authorization code in external repositories; they are
not RepoGuard permissions.

## Architecture

```text
Next.js browser
    │ same-origin REST + EventSource (access cookie)
    ▼
Nginx ───────────────► FastAPI API
                         │
                         ├── services → PostgreSQL repositories
                         ├── AI trace / summaries / evidence → MongoDB
                         ├── progress Pub/Sub + snapshots → Redis
                         └── review jobs → Celery worker
```

Routes validate input and enforce authentication; services own business rules;
repositories own persistence. The worker pipeline clones a repository, analyzes
structure, generates a project summary, runs static analyzers, chunks and
embeds code, performs probe/KB-grounded review, and persists the report.

## Technology stack

- Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic,
  Celery, Redis, PostgreSQL, MongoDB, ChromaDB.
- Frontend: Next.js 15, React 19, TypeScript, Tailwind CSS, Redux Toolkit,
  Axios, native `EventSource` (no realtime dependency).
- Analysis: Ruff, Bandit, ESLint, secret scanning, hybrid KB retrieval, and
  repository code-embedding search.

## Prerequisites

- Docker Desktop with Compose v2 (recommended), or Python 3.11+, Node 20+,
  PostgreSQL 16, Redis 7, and MongoDB 7 for a native setup.
- Git available to the backend worker.
- An LLM credential for reviews (`OPENAI_API_KEY`, or the configured provider)
  and any configured code-embedding credential.

## Environment

Copy the template and fill secrets:

```powershell
Copy-Item .env.example .env
```

At minimum set `POSTGRES_PASSWORD`, `MONGODB_PASSWORD`, and a random
`JWT_SECRET_KEY` of at least 32 characters. For the Docker layout in this
repository, PostgreSQL and Redis run in the external infrastructure Compose
file, while the root Compose file starts MongoDB and the application stack.
The values below are the important Docker-to-host settings (replace the
password):

```dotenv
POSTGRES_URL=postgresql+asyncpg://repoguard:<password>@host.docker.internal:15432/repoguard_ai
REDIS_URL=redis://host.docker.internal:6379/0
CELERY_BROKER_URL=redis://host.docker.internal:6379/0
CELERY_RESULT_BACKEND=redis://host.docker.internal:6379/0
MONGODB_URL=mongodb://repoguard:<password>@mongodb:27017/repoguard_ai?authSource=admin
```

Do not commit `.env`; secrets are intentionally absent from `.env.example`.

## Docker setup (recommended)

Run these commands from the repository root. The infrastructure file starts
host-mapped PostgreSQL/Redis, and the root file starts MongoDB plus the
application services; PostgreSQL/Redis can remain up while the application
stack is restarted.

```powershell
# Start the external PostgreSQL and Redis used by the root stack.
docker compose --env-file .env -f docker/docker-compose.yml up -d postgres redis

# Build the disposable fix executor and start the application services.
docker compose up --build -d

# Apply all migrations inside the backend container.
docker compose exec backend alembic upgrade head

# Seed the knowledge base (optional but recommended before the first review).
docker compose run --rm -v "${PWD}:/workspace" -w /workspace backend `
  python scripts/seed_rag.py --skip-smoke
```

Open `http://localhost` (or the configured `NGINX_HOST_PORT`). Direct service
ports are controlled by `BACKEND_HOST_PORT`, `FRONTEND_HOST_PORT`, and the
Compose files. The worker mounts the Docker socket only to launch the restricted,
one-command fix executor against the shared sandbox volume. Keep
`FIX_EXECUTOR_NETWORK=none` unless locked dependency installation explicitly needs
network access. To stop the application stack without removing data:

```powershell
docker compose down

# Stop PostgreSQL and Redis as well when they are no longer needed.
docker compose --env-file .env -f docker/docker-compose.yml down
```

## Native development setup

Install backend development dependencies from `backend/` and frontend
dependencies from `frontend/`:

```powershell
cd backend
python -m pip install -r requirements-dev.txt

cd ../frontend
npm ci
```

Then point `POSTGRES_URL`, `REDIS_URL`, and `MONGODB_URL` at your local services
(use `localhost` when running processes directly on the host). Run the API and
worker in separate terminals:

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
celery -A app.worker worker --loglevel=info --pool=solo
```

In another terminal:

```powershell
cd frontend
npm run dev
```

Use `http://localhost:3000` for the frontend and configure `API_PROXY_TARGET`
if the Next.js development proxy is not targeting `http://backend:8000`.

## Database migrations and RAG seeding

```powershell
cd backend
alembic upgrade head
```

Migration `20260728_0008_normalize_user_roles` converts legacy `admin` accounts
to `user`; the legacy PostgreSQL enum label is deliberately retained. Seed the
knowledge base from the repository root:

```powershell
python scripts/seed_rag.py --skip-smoke
```

The knowledge-base embedding runtime uses Chroma's ONNX build of
`all-MiniLM-L6-v2`. After upgrading from the former PyTorch embedding image,
rebuild an existing local collection once with `--reset`.

The seed command ingests the manifest sources and the current probe/KB roadmap
documents. It does not alter the review pipeline or code-embedding cache.

## Tests and quality checks

Backend:

```powershell
cd backend
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy .
```

The three-job Redis integration test is opt-in so normal CI does not require a
running service:

```powershell
$env:TEST_REDIS_URL = "redis://localhost:6379/0"
python -m pytest -m integration -q
```

Frontend:

```powershell
cd frontend
npm test
npm run lint
npx tsc --noEmit --incremental false
npm run build
```

## SSE progress contract

The owner-authenticated endpoint is:

```text
GET /api/review-jobs/{job_id}/stream
Accept: text/event-stream
Cookie: accessToken=<HttpOnly access cookie>
```

The browser uses native `EventSource` with same-origin credentials. Each event
has the normalized JSON shape:

```json
{
  "job_id": "uuid",
  "event": "progress_update",
  "status": "AI_REVIEWING",
  "progress": 72,
  "message": "Reviewing code",
  "timestamp": "2026-07-28T10:00:00Z",
  "data": {}
}
```

Named events are `status_change`, `progress_update`, `log`, `completed`, and
`failed`. The stream sends a browser retry hint, replays the latest Redis
snapshot, emits a `: keep-alive` heartbeat every 15 seconds, and closes after a
terminal event. Redis uses channel `job:{job_id}:progress` and snapshot key
`job:{job_id}:progress:last` with a one-hour TTL. The Nginx stream location has
`proxy_buffering off` and `X-Accel-Buffering: no`.

## Demo flow

1. Register and log in as a normal user.
2. Add a GitHub or GitLab repository and select its branch.
3. Start a review from the Reviews page.
4. Open the job detail page to see the progress card and live connection state.
5. Wait for `completed` (or inspect a `failed` terminal event), then open the
   report, issues, repository summary, and AI trace tabs.
6. Refresh the page or reconnect the browser to verify snapshot replay; access
   another user's job ID to verify owner-only authorization.

## Troubleshooting

- **Database health check fails:** verify `POSTGRES_URL` uses
  `host.docker.internal:15432` for the default infrastructure Compose port and
  that `docker compose -f docker/docker-compose.yml ps` shows PostgreSQL healthy.
- **Redis health check fails:** verify Redis is listening on host port 6379 and
  that `REDIS_URL` and both Celery URLs use the same database/credentials.
- **MongoDB authentication fails:** inside the root stack use hostname
  `mongodb`, port 27017, and `authSource=admin`; do not use the host-mapped
  port from inside the container network.
- **No AI progress:** check the backend and worker logs, LLM credentials, and
  the initial RAG seed. The AI trace has its own REST refresh loop and is
  intentionally separate from the progress stream.
- **SSE reconnects repeatedly:** inspect the access/refresh cookies and API
  logs. A failed refresh follows the normal logout flow; Nginx must route the
  `/api/review-jobs/.../stream` location without buffering.
- **First run is slow:** ChromaDB and embedding/LLM models may download on the
  first seed or review. Keep the worker alive until the download completes.

Screenshots, video capture, WebSockets, and a notification badge are not part
of the current scope.
