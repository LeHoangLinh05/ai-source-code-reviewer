"""
Sinh backend/app/ai/rules/roadmap_rules_v2.yaml — nguồn duy nhất (single source of truth)
cho toàn bộ 79 Roadmap Compliance Rules.

Cách dùng: sửa dữ liệu rule ở phần "1. ĐỊNH NGHĨA RULE" hoặc "2. TARGET CHO TỪNG RULE"
bên dưới, rồi chạy: python3 scripts/build_rules_yaml.py
KHÔNG sửa tay file roadmap_rules_v2.yaml — nó luôn bị ghi đè khi chạy lại script này.

Nguồn nội dung rule: Lộ_trình_đào_tạo_Python_NextJS_AI_agent.xlsx (Tuần 1-7 + Final Project).
Spec đầy đủ (rationale thiết kế, scoring, provisional_pass mechanism): xem AI_flow.md mục 3.
"""

import yaml  # type: ignore[import-untyped]

OUTPUT_PATH = "backend/app/ai/rules/roadmap_rules_v2.yaml"

SKILL_GROUP = {
    1: "Backend Core & JWT Auth",
    2: "DevOps & Docker",
    3: "Database Layer (MySQL + MongoDB + Redis)",
    4: "Frontend Next.js 15",
    5: "Realtime Communication",
    6: "AI Chatbox & Context Memory",
    7: "RAG (Retrieval-Augmented Generation)",
    "GEN": "General / Final Project",
}
SEVERITY = {"P0": "critical", "P1": "high", "P2": "low"}

# =============================================================================
# 1. ĐỊNH NGHĨA RULE — (id, week, requirement, check_type, priority, ai_verify, ai_hint)
# =============================================================================
_rules: list[dict[str, object]] = []


def R(id, week, req, ctype, prio, ai=False, hint=None):
    _rules.append(
        dict(id=id, week=week, req=req, ctype=ctype, prio=prio, ai=ai, hint=hint)
    )


# --- Tuần 1 — Backend Core & JWT Auth ---
R("RC-W1-01", 1, "Dùng FastAPI", "required_dependency", "P0")
R("RC-W1-02", 1, "Dùng Pydantic (schema validation)", "required_dependency", "P0")
R("RC-W1-03", 1, "Dùng Uvicorn", "required_dependency", "P0")
R("RC-W1-04", 1, "Có thư viện JWT", "required_dependency", "P0")
R("RC-W1-05", 1, "Tách thư mục routers/", "required_folder", "P0")
R("RC-W1-06", 1, "Tách thư mục schemas/", "required_folder", "P0")
R("RC-W1-07", 1, "Tách thư mục models/", "required_folder", "P0")
R(
    "RC-W1-08",
    1,
    "Tách thư mục utils/ (jwt_handler, password_hash)",
    "required_folder",
    "P1",
)
R(
    "RC-W1-09",
    1,
    "Endpoint /register (password phải hash)",
    "required_code_pattern",
    "P0",
)
R(
    "RC-W1-10",
    1,
    "Endpoint /login trả access+refresh token",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận /login thực sự verify password hash và sinh đúng JWT 2 tầng (access + refresh) "
    "với payload/expiry hợp lệ — không phải hardcode token giả để pass check.",
)
R(
    "RC-W1-11",
    1,
    "Endpoint /token/refresh",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận endpoint validate refresh_token (chữ ký, hạn dùng, chưa revoke) TRƯỚC khi cấp "
    "access_token mới — không cấp vô điều kiện.",
)
R("RC-W1-12", 1, "Endpoint /users/me", "required_code_pattern", "P1")
R(
    "RC-W1-13",
    1,
    "Có logout / vô hiệu hoá token",
    "required_code_pattern",
    "P1",
    True,
    "Xác nhận logout thực sự vô hiệu hoá token (xoá refresh_token khỏi DB/Redis hoặc thêm "
    "blacklist) — không chỉ trả 200 OK mà không làm gì.",
)
R("RC-W1-14", 1, "Có logging", "required_code_pattern", "P1")
R("RC-W1-15", 1, "Có exception handler chuẩn REST", "required_code_pattern", "P1")
R("RC-W1-16", 1, "README hướng dẫn setup", "required_file", "P1")
R(
    "RC-W1-17",
    1,
    "Password được hash trước khi lưu DB",
    "required_dependency",
    "P0",
    True,
    "Xác nhận password THỰC SỰ được hash tại điểm gọi trong route /register trước khi insert "
    "DB — không lưu plaintext dù có import thư viện hash.",
)

# --- Tuần 2 — DevOps & Docker ---
R("RC-W2-01", 2, "Dockerfile.dev cho Backend", "required_any_of", "P0")
R("RC-W2-02", 2, "Dockerfile.prod cho Backend", "required_any_of", "P0")
R("RC-W2-03", 2, "Dockerfile.dev cho Frontend", "required_any_of", "P0")
R("RC-W2-04", 2, "Dockerfile.prod cho Frontend", "required_any_of", "P0")
R("RC-W2-05", 2, "docker-compose.yml ở root", "required_any_of", "P0")
R(
    "RC-W2-06",
    2,
    "Compose có service DB (MySQL/Mongo/Redis)",
    "required_config_key",
    "P0",
)
R("RC-W2-07", 2, "Có Nginx reverse proxy config", "required_file", "P1")
R("RC-W2-08", 2, ".env KHÔNG được commit", "forbidden_tracked_file", "P0")
R("RC-W2-09", 2, "Có .env.example", "required_file", "P1")
R("RC-W2-10", 2, "Compose có healthcheck", "required_config_key", "P2")
R("RC-W2-11", 2, "Nginx có cấu hình SSL/TLS", "required_config_key", "P2")
R(
    "RC-W2-12",
    2,
    "README có link demo triển khai (Render/Railway/EC2)",
    "required_code_pattern",
    "P2",
)

# --- Tuần 3 — Database Layer ---
R("RC-W3-01", 3, "Dùng SQLAlchemy ORM", "required_dependency", "P0")
R("RC-W3-02", 3, "Dùng Alembic migration (thư mục alembic/)", "required_folder", "P0")
R("RC-W3-03", 3, "Có ≥ 2 revision Alembic", "min_file_count", "P0")
R("RC-W3-04", 3, "Dùng MongoDB (PyMongo/Motor)", "required_dependency", "P0")
R("RC-W3-05", 3, "Dùng Redis client", "required_dependency", "P0")
R(
    "RC-W3-06",
    3,
    "Cache GET /products với TTL",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận TTL cụ thể ~30s theo đề bài, và cache áp dụng đúng cho response GET /products "
    "(không phải cache 1 endpoint không liên quan để pass check).",
)
R(
    "RC-W3-07",
    3,
    "Cache bị xoá khi có CRUD thay đổi dữ liệu",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận cache bị xoá NGAY khi tạo/sửa/xoá sản phẩm — không phải chỉ hết hạn tự nhiên "
    "theo TTL.",
)
R(
    "RC-W3-08",
    3,
    "Có RBAC (Role, Permission, Role_Permission)",
    "required_code_pattern",
    "P1",
)
R(
    "RC-W3-09",
    3,
    "Có job sync định kỳ MySQL → MongoDB",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận: (a) job chạy ĐỊNH KỲ thật (celery beat/cron/scheduler, không phải hàm gọi tay); "
    "(b) dùng timestamp/version để chỉ đồng bộ phần THAY ĐỔI (incremental) — không xoá-ghi-lại "
    "toàn bộ mỗi lần chạy.",
)
R(
    "RC-W3-10",
    3,
    "Lưu lịch sử sync vào collection sync_logs",
    "required_code_pattern",
    "P1",
)
R(
    "RC-W3-11",
    3,
    "Alembic có revision loại create table VÀ alter table",
    "required_code_pattern",
    "P1",
)
R("RC-W3-12", 3, "Có benchmark cache vs không cache", "required_code_pattern", "P2")

# --- Tuần 4 — Frontend Next.js 15 ---
R("RC-W4-01", 4, "Dùng Next.js 15", "required_dependency", "P0")
R("RC-W4-02", 4, "Dùng TailwindCSS", "required_dependency", "P0")
R("RC-W4-03", 4, "Dùng Axios", "required_dependency", "P1")
R("RC-W4-04", 4, "Dùng App Router", "required_folder", "P0")
R(
    "RC-W4-05",
    4,
    "Có trang Login + Protected Route",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận middleware/guard THỰC SỰ redirect khi chưa đăng nhập — không chỉ được import "
    "nhưng chưa gắn vào route nào.",
)
R("RC-W4-06", 4, "Dùng TypeScript", "required_file", "P1")
R("RC-W4-07", 4, "Có biểu đồ (chart)", "required_dependency", "P1")
R("RC-W4-08", 4, "Có sơ đồ Mermaid", "required_dependency", "P2")
R(
    "RC-W4-09",
    4,
    "Bảng dữ liệu lớn: pagination/infinite/virtual scroll",
    "required_dependency",
    "P1",
)
R("RC-W4-10", 4, "Có Skeleton loading", "required_code_pattern", "P2")
R("RC-W4-11", 4, "Có .env.local hoặc .env.local.example", "required_any_of", "P1")

# --- Tuần 5 — Realtime ---
R(
    "RC-W5-01",
    5,
    "Có SSE hoặc WebSocket, broadcast nhiều user",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận broadcast được tới NHIỀU client đồng thời, không phải chỉ echo 1-1 giữa 1 client "
    "và server.",
)
R(
    "RC-W5-02",
    5,
    "Có Connection Manager quản lý nhiều client",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận có cấu trúc lưu danh sách connections (dict/list theo room hoặc user) — không "
    "phải 1 biến global single-connection.",
)
R(
    "RC-W5-03",
    5,
    "JWT được validate khi connect realtime",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận server TỪ CHỐI kết nối khi token invalid (đóng connection/close code) — không "
    "chỉ decode token rồi bỏ qua lỗi nếu decode fail.",
)
R(
    "RC-W5-04",
    5,
    "Redis Pub/Sub đồng bộ multi-instance",
    "required_code_pattern",
    "P1",
    True,
    "Xác nhận publish/subscribe dùng CHUNG channel và message thực sự được broadcast tới "
    "client ở instance khác — không chỉ khai báo publish/subscribe riêng lẻ không khớp channel.",
)
R("RC-W5-05", 5, "Tin nhắn/realtime data được lưu DB", "required_code_pattern", "P1")
R(
    "RC-W5-06",
    5,
    "Frontend có hook/client kết nối realtime",
    "required_code_pattern",
    "P1",
)
R("RC-W5-07", 5, "(Tự học) WebRTC video call", "required_code_pattern", "P2")

# --- Tuần 6 — AI Chatbox với Context Memory ---
R(
    "RC-W6-01",
    6,
    "Lịch sử hội thoại lưu theo user_id/session_id",
    "required_code_pattern",
    "P0",
)
R(
    "RC-W6-02",
    6,
    "Có cơ chế memory (buffer/summary)",
    "required_code_pattern",
    "P0",
    True,
    "Đây là tiêu chí nặng nhất tuần 6 (25%). Xác nhận context được nối/tóm tắt HỢP LÝ khi hội "
    "thoại dài — KHÔNG lặp lại nội dung cũ thừa mỗi lần gọi LLM, không phải chỉ nối chuỗi thô "
    "không giới hạn.",
)
R(
    "RC-W6-03",
    6,
    "Có tool/function calling schema cho AI",
    "required_code_pattern",
    "P1",
)
R(
    "RC-W6-04",
    6,
    "Có endpoint reset/clear session",
    "required_code_pattern",
    "P1",
    True,
    "Xác nhận endpoint thực sự xoá context đã lưu (DB/Redis) — không chỉ trả 200 OK mà dữ "
    "liệu cũ vẫn còn.",
)
R("RC-W6-05", 6, "UI chat realtime (dùng lại tuần 5)", "required_code_pattern", "P1")
R("RC-W6-06", 6, "Có logging hội thoại/lỗi", "required_code_pattern", "P2")
R(
    "RC-W6-07",
    6,
    "UI phân biệt AI vs User message + hiệu ứng loading/typing",
    "required_code_pattern",
    "P2",
)

# --- Tuần 7 — RAG ---
R("RC-W7-01", 7, "Dùng Vector DB", "required_dependency", "P0")
R("RC-W7-02", 7, "Có embedding pipeline", "required_code_pattern", "P0")
R("RC-W7-03", 7, "Có chunking/text splitter", "required_code_pattern", "P0")
R(
    "RC-W7-04",
    7,
    "Có pipeline retrieve → LLM → response",
    "required_code_pattern",
    "P0",
    True,
    "Xác nhận kết quả retrieve THỰC SỰ được đưa vào prompt gửi LLM — không phải gọi retriever "
    "nhưng bỏ qua kết quả (retrieve xong không dùng).",
)
R(
    "RC-W7-05",
    7,
    "Top-K / similarity threshold cấu hình được",
    "required_code_pattern",
    "P1",
)
R(
    "RC-W7-06",
    7,
    "Kết hợp Memory + RAG",
    "required_code_pattern",
    "P1",
    True,
    "Xác nhận CẢ HAI (memory hội thoại + RAG retrieval) cùng góp mặt trong 1 lượt trả lời — "
    "không phải chỉ dùng 1 trong 2 rồi gọi là hybrid.",
)
R(
    "RC-W7-07",
    7,
    "Có debug mode xem context + RAG result",
    "required_code_pattern",
    "P2",
)
R("RC-W7-08", 7, "Cache câu hỏi lặp lại bằng Redis", "required_code_pattern", "P2")

# --- General / Final Project ---
R("RC-GEN-01", "GEN", "Backend viết bằng Python", "required_dependency", "P0")
R(
    "RC-GEN-02",
    "GEN",
    "Có .gitignore loại trừ .env, node_modules/, venv/",
    "required_config_key",
    "P1",
)
R(
    "RC-GEN-03",
    "GEN",
    "Kết hợp ≥ 2/3 DB (SQL/Mongo/Redis) — bonus final project",
    "required_config_key",
    "P2",
)
R("RC-GEN-04", "GEN", "Có CI cơ bản", "required_folder", "P2")
R(
    "RC-GEN-05",
    "GEN",
    "docker-compose up chạy toàn bộ stack 1 lệnh",
    "required_config_key",
    "P1",
)


# =============================================================================
# 2. TARGET CHO TỪNG RULE — cấu trúc thật để checker (Python) đọc trực tiếp
# =============================================================================
PY_MANIFEST = ["**/requirements.txt", "**/pyproject.toml"]
JS_MANIFEST = ["**/package.json"]
PY_GLOB = ["**/*.py"]
JS_GLOB = ["**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"]
AI_GLOB = ["**/ai/**/*.py"]
RAG_GLOB = ["**/rag/**/*.py"]
AI_RAG_GLOB = [*AI_GLOB, *RAG_GLOB]

TARGETS = {
    "RC-W1-01": {"manifest_glob": list(PY_MANIFEST), "package_any_of": ["fastapi"]},
    "RC-W1-02": {"manifest_glob": list(PY_MANIFEST), "package_any_of": ["pydantic"]},
    "RC-W1-03": {"manifest_glob": list(PY_MANIFEST), "package_any_of": ["uvicorn"]},
    "RC-W1-04": {
        "manifest_glob": list(PY_MANIFEST),
        "package_any_of": ["pyjwt", "python-jose"],
    },
    "RC-W1-05": {"glob": ["**/routers/"]},
    "RC-W1-06": {"glob": ["**/schemas/"]},
    "RC-W1-07": {"glob": ["**/models/"]},
    "RC-W1-08": {"glob": ["**/utils/"]},
    "RC-W1-09": {"glob": list(PY_GLOB), "regex": r"/register", "min_matches": 1},
    "RC-W1-10": {
        "glob": list(PY_GLOB),
        "regex_all_of": [r"/login", r"refresh_token"],
        "min_matches": 1,
    },
    "RC-W1-11": {
        "glob": list(PY_GLOB),
        "regex": r"/token/refresh|/refresh",
        "min_matches": 1,
    },
    "RC-W1-12": {"glob": list(PY_GLOB), "regex": r"/users/me", "min_matches": 1},
    "RC-W1-13": {
        "glob": list(PY_GLOB),
        "regex": r"/logout|blacklist",
        "min_matches": 1,
    },
    "RC-W1-14": {
        "glob": list(PY_GLOB),
        "regex": r"^import logging|loguru",
        "min_matches": 1,
    },
    "RC-W1-15": {
        "glob": list(PY_GLOB),
        "regex": r"HTTPException|exception_handler",
        "min_matches": 1,
    },
    "RC-W1-16": {"glob": ["**/README.md"]},
    "RC-W1-17": {
        "manifest_glob": list(PY_MANIFEST),
        "package_any_of": ["bcrypt", "passlib", "argon2-cffi"],
    },
    "RC-W2-01": {"glob_any_of": ["**/backend/Dockerfile.dev", "**/Dockerfile.dev"]},
    "RC-W2-02": {"glob_any_of": ["**/backend/Dockerfile.prod", "**/Dockerfile.prod"]},
    "RC-W2-03": {"glob_any_of": ["**/frontend/Dockerfile.dev"]},
    "RC-W2-04": {"glob_any_of": ["**/frontend/Dockerfile.prod"]},
    "RC-W2-05": {
        "glob_any_of": [
            "docker-compose.yml",
            "docker-compose.yaml",
            "docker/docker-compose.yml",
        ]
    },
    "RC-W2-06": {
        "file_glob": ["docker-compose.yml", "docker-compose.yaml"],
        "key_pattern": r"mysql|postgres|mongo|redis",
    },
    "RC-W2-07": {"glob": ["**/nginx/nginx.conf", "**/nginx.conf"]},
    "RC-W2-08": {"glob": [".env"]},
    "RC-W2-09": {"glob": ["**/.env.example"]},
    "RC-W2-10": {
        "file_glob": ["docker-compose.yml", "docker-compose.yaml"],
        "key_pattern": r"healthcheck:",
    },
    "RC-W2-11": {"file_glob": ["**/nginx.conf"], "key_pattern": r"ssl_certificate"},
    "RC-W2-12": {"glob": ["**/README.md"], "regex": r"https?://", "min_matches": 1},
    "RC-W3-01": {"manifest_glob": list(PY_MANIFEST), "package_any_of": ["sqlalchemy"]},
    "RC-W3-02": {"glob": ["**/alembic/"]},
    "RC-W3-03": {"glob": ["**/alembic/versions/*.py"], "min_count": 2},
    "RC-W3-04": {
        "manifest_glob": list(PY_MANIFEST),
        "package_any_of": ["pymongo", "motor"],
    },
    "RC-W3-05": {
        "manifest_glob": list(PY_MANIFEST),
        "package_any_of": ["redis", "aioredis"],
    },
    "RC-W3-06": {
        "glob": list(PY_GLOB),
        "regex": r"\.expire\(|setex\(|ttl=",
        "min_matches": 1,
    },
    "RC-W3-07": {
        "glob": list(PY_GLOB),
        "regex": r"cache\.delete|invalidate_cache|cache_delete",
        "min_matches": 1,
    },
    "RC-W3-08": {
        "glob": list(PY_GLOB),
        "regex_all_of": [r"class Role", r"class Permission"],
        "min_matches": 1,
    },
    "RC-W3-09": {
        "glob": ["**/workers/**/*.py", "**/services/**/*.py"],
        "regex": r"sync",
        "min_matches": 1,
    },
    "RC-W3-10": {"glob": list(PY_GLOB), "regex": r"sync_logs", "min_matches": 1},
    "RC-W3-11": {
        "glob": ["**/alembic/versions/*.py"],
        "regex_all_of": [r"op\.create_table", r"op\.alter_column|op\.add_column"],
        "min_matches": 1,
    },
    "RC-W3-12": {
        "glob": list(PY_GLOB),
        "regex": r"benchmark|query_time|elapsed_time",
        "min_matches": 1,
    },
    "RC-W4-01": {
        "manifest_glob": list(JS_MANIFEST),
        "package_any_of": ["next"],
        "version_constraint": "^15",
    },
    "RC-W4-02": {"manifest_glob": list(JS_MANIFEST), "package_any_of": ["tailwindcss"]},
    "RC-W4-03": {"manifest_glob": list(JS_MANIFEST), "package_any_of": ["axios"]},
    "RC-W4-04": {"glob": ["**/app/layout.tsx", "**/app/layout.jsx"]},
    "RC-W4-05": {
        "glob": ["**/middleware.ts", "**/middleware.js", "**/*auth*guard*.tsx"],
        "regex": r"middleware|NextResponse\.redirect|redirect\(",
        "min_matches": 1,
    },
    "RC-W4-06": {"glob": ["**/tsconfig.json"]},
    "RC-W4-07": {
        "manifest_glob": list(JS_MANIFEST),
        "package_any_of": ["recharts", "chart.js"],
    },
    "RC-W4-08": {"manifest_glob": list(JS_MANIFEST), "package_any_of": ["mermaid"]},
    "RC-W4-09": {
        "manifest_glob": list(JS_MANIFEST),
        "package_any_of": [
            "@tanstack/react-table",
            "react-window",
            "react-virtualized",
        ],
    },
    "RC-W4-10": {"glob": list(JS_GLOB), "regex": r"(?i)skeleton", "min_matches": 1},
    "RC-W4-11": {"glob_any_of": ["**/.env.local", "**/.env.local.example"]},
    "RC-W5-01": {
        "glob": list(PY_GLOB),
        "regex": r"websocket|WebSocket|text/event-stream|EventSourceResponse",
        "min_matches": 1,
    },
    "RC-W5-02": {
        "glob": list(PY_GLOB),
        "regex": r"ConnectionManager",
        "min_matches": 1,
    },
    "RC-W5-03": {
        "glob": list(PY_GLOB),
        "regex": r"websocket|WebSocket",
        "min_matches": 1,
    },
    "RC-W5-04": {
        "glob": list(PY_GLOB),
        "regex_all_of": [r"\.publish\(", r"\.subscribe\("],
        "min_matches": 1,
    },
    "RC-W5-05": {
        "glob": list(PY_GLOB),
        "regex": r"class Message|class ChatLog|chat_history",
        "min_matches": 1,
    },
    "RC-W5-06": {
        "glob": list(JS_GLOB),
        "regex": r"useWebSocket|EventSource|WebSocket\(",
        "min_matches": 1,
    },
    "RC-W5-07": {
        "glob": list(JS_GLOB),
        "regex": r"RTCPeerConnection",
        "min_matches": 1,
    },
    "RC-W6-01": {
        "glob": list(PY_GLOB),
        "regex_all_of": [r"chat_history|conversation", r"user_id"],
        "min_matches": 1,
    },
    "RC-W6-02": {
        "glob": list(PY_GLOB),
        "regex": r"Memory|ConversationBuffer|conversation_summary",
        "min_matches": 1,
    },
    "RC-W6-03": {
        "glob": list(PY_GLOB),
        "regex": r"tools=|function_call",
        "min_matches": 1,
    },
    "RC-W6-04": {"glob": list(PY_GLOB), "regex": r"/reset|/clear", "min_matches": 1},
    "RC-W6-05": {
        "glob": list(JS_GLOB),
        "regex": r"WebSocket|EventSource",
        "min_matches": 1,
    },
    "RC-W6-06": {
        "glob": list(PY_GLOB),
        "regex": r"logging\.|logger\.",
        "min_matches": 1,
    },
    "RC-W6-07": {
        "glob": list(JS_GLOB),
        "regex": r"role\s*===?\s*['\"](assistant|user)['\"]",
        "min_matches": 1,
    },
    "RC-W7-01": {
        "manifest_glob": list(PY_MANIFEST),
        "package_any_of": ["chromadb", "faiss-cpu", "qdrant-client"],
    },
    "RC-W7-02": {
        "glob": list(AI_RAG_GLOB),
        "regex": r"embed|Embeddings",
        "min_matches": 1,
    },
    "RC-W7-03": {
        "glob": list(AI_RAG_GLOB),
        "regex": r"TextSplitter|chunk",
        "min_matches": 1,
    },
    "RC-W7-04": {
        "glob": list(PY_GLOB),
        "regex": r"retriev|RAG|RetrievedChunk|search_coding_standard",
        "min_matches": 1,
    },
    "RC-W7-05": {
        "glob": list(AI_RAG_GLOB),
        "regex": r"top_k|similarity_threshold",
        "min_matches": 1,
    },
    "RC-W7-06": {
        "glob": list(AI_RAG_GLOB),
        "regex_all_of": [r"Memory|conversation", r"retriev"],
        "min_matches": 1,
    },
    "RC-W7-07": {
        "glob": list(AI_GLOB),
        "regex": r"retrieved_chunks|debug.*context",
        "min_matches": 1,
    },
    "RC-W7-08": {
        "glob": list(AI_GLOB),
        "regex": r"query_hash|cache_key",
        "min_matches": 1,
    },
    "RC-GEN-01": {"glob_any_of": ["**/requirements.txt", "**/pyproject.toml"]},
    "RC-GEN-02": {"file_glob": [".gitignore"], "key_pattern": r"\.env"},
    "RC-GEN-03": {
        "file_glob": ["docker-compose.yml", "docker-compose.yaml"],
        "key_pattern": r"(mysql|postgres).*(mongo).*(redis)|redis.*mongo",
    },
    "RC-GEN-04": {"glob": [".github/workflows/"]},
    "RC-GEN-05": {
        "file_glob": ["docker-compose.yml", "docker-compose.yaml"],
        "key_pattern": r"services:",
    },
}


# =============================================================================
# 3. BUILD + VALIDATE + GHI FILE
# =============================================================================
def build():
    output_rules = []
    for r in _rules:
        week = r["week"]
        prio = r["prio"]
        target = TARGETS[r["id"]]
        rationale = (
            f"Lộ trình {'Tuần ' + str(week) if week != 'GEN' else 'General/Final Project'} "
            f"({SKILL_GROUP[week]}) yêu cầu: {r['req']}. Không tìm thấy bằng chứng "
            f"(file/dependency/pattern) tương ứng trong repo."
        )
        rule = {
            "rule_id": r["id"],
            "week": week,
            "skill_group": SKILL_GROUP[week],
            "requirement": r["req"],
            "check_type": r["ctype"],
            "target": target,
            "priority": prio,
            "severity_if_missing": SEVERITY[prio],
            "category": "requirement",
            "source": "roadmap_rule",
            "confidence": 1.0,
            "rationale_if_missing": rationale,
            "needs_ai_verification": r["ai"],
        }
        if r["ai"]:
            rule["ai_hint"] = r["hint"]
        output_rules.append(rule)
    return output_rules


def validate(output_rules):
    assert len(output_rules) == 79, f"Kỳ vọng 79 rules, có {len(output_rules)}"
    ids = [r["rule_id"] for r in output_rules]
    assert len(set(ids)) == len(ids), "Có rule_id trùng lặp"
    missing = set(TARGETS) - set(ids)
    assert not missing, f"TARGETS có id không khớp rule nào: {missing}"
    for r in output_rules:
        if r["needs_ai_verification"]:
            assert "ai_hint" in r, (
                f"{r['rule_id']}: needs_ai_verification=true nhưng thiếu ai_hint"
            )
        else:
            assert "ai_hint" not in r, (
                f"{r['rule_id']}: needs_ai_verification=false nhưng có ai_hint thừa"
            )


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


HEADER = """# =============================================================================
# roadmap_rules_v2.yaml — Roadmap Compliance Rule Set (79 rules)
# Nguồn: Lộ_trình_đào_tạo_Python_NextJS_AI_agent.xlsx (Tuần 1-7 + Mục tiêu/Final Project)
# Sinh ra từ scripts/build_rules_yaml.py — KHÔNG sửa tay file này, sửa script rồi chạy lại.
# Spec đầy đủ (rationale, scoring, provisional_pass mechanism): xem AI_flow.md mục 3.
#
# QUY ƯỚC GLOB (áp dụng cho mọi rule bên dưới):
#   - Checker tự loại trừ mặc định: node_modules/, .git/, venv/, .venv/, __pycache__/,
#     dist/, build/, .next/  (KHÔNG cần khai báo lại trong từng rule)
#   - "**/" ở đầu pattern = tìm ở BẤT KỲ độ sâu nào trong repo (không giả định tên thư mục
#     backend/ hay frontend/ cố định, vì repo học viên có thể đặt tên khác nhau)
#
# CÁC KEY CÓ THỂ XUẤT HIỆN TRONG "target" TUỲ check_type:
#   required_file / required_folder / forbidden_tracked_file:
#     glob: [<pattern>, ...]              -> pass nếu CÓ match (forbidden: pass nếu KHÔNG match)
#   required_any_of:
#     glob_any_of: [<pattern>, ...]       -> pass nếu ít nhất 1 pattern có match
#   required_dependency:
#     manifest_glob: [<pattern>, ...]     -> nơi tìm (requirements.txt/package.json/...)
#     package_any_of: [<tên_package>, ...] -> pass nếu manifest chứa 1 trong các tên này
#     version_constraint: "<vd: ^15>"     -> optional, so khớp version nếu có khai báo
#   required_code_pattern:
#     glob: [<pattern>, ...]              -> phạm vi file quét regex
#     regex: "<pattern>"                  -> 1 regex duy nhất (có thể dùng | để OR)
#     regex_all_of: ["<p1>", "<p2>", ...] -> TẤT CẢ pattern đều phải xuất hiện (có thể khác file)
#     min_matches: N                       -> số lần match tối thiểu (áp dụng cho regex đơn)
#   min_file_count:
#     glob: [<pattern>, ...]
#     min_count: N
#   required_config_key:
#     file_glob: [<pattern>, ...]         -> file config cần đọc
#     key_pattern: "<pattern>"            -> nội dung phải chứa pattern này
# =============================================================================

"""


def main():
    output_rules = build()
    validate(output_rules)
    body = yaml.dump(
        {"rules": output_rules},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        Dumper=NoAliasDumper,
    )
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(HEADER + body)
    print(f"OK — wrote {len(output_rules)} rules to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
