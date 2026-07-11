# Danh sách các quy tắc (Rules/Plans) cần AI Review / Verification

Tài liệu này tổng hợp toàn bộ các quy tắc trong lộ trình học tập yêu cầu **AI Verification** (kiểm tra chuyên sâu bằng AI) thay vì chỉ kiểm tra tĩnh (static analysis) bằng Regex hoặc Dependency checker.

Tổng số quy tắc cần AI review: **16**

## Tóm tắt nhanh

| ID | Tuần | Nhóm Kỹ Năng | Yêu Cầu (Requirement) | Độ ưu tiên |
| :--- | :--- | :--- | :--- | :--- |
| `RC-W1-10` | Tuần 1 | Backend Core & JWT Auth | Endpoint /login trả access+refresh token | **P0** |
| `RC-W1-11` | Tuần 1 | Backend Core & JWT Auth | Endpoint /token/refresh | **P0** |
| `RC-W1-13` | Tuần 1 | Backend Core & JWT Auth | Có logout / vô hiệu hoá token | **P1** |
| `RC-W1-17` | Tuần 1 | Backend Core & JWT Auth | Password được hash trước khi lưu DB | **P0** |
| `RC-W3-06` | Tuần 3 | Database Layer (MySQL + MongoDB + Redis) | Cache GET /products với TTL | **P0** |
| `RC-W3-07` | Tuần 3 | Database Layer (MySQL + MongoDB + Redis) | Cache bị xoá khi có CRUD thay đổi dữ liệu | **P0** |
| `RC-W3-09` | Tuần 3 | Database Layer (MySQL + MongoDB + Redis) | Có job sync định kỳ MySQL → MongoDB | **P0** |
| `RC-W4-05` | Tuần 4 | Frontend Next.js 15 | Có trang Login + Protected Route | **P0** |
| `RC-W5-01` | Tuần 5 | Realtime Communication | Có SSE hoặc WebSocket, broadcast nhiều user | **P0** |
| `RC-W5-02` | Tuần 5 | Realtime Communication | Có Connection Manager quản lý nhiều client | **P0** |
| `RC-W5-03` | Tuần 5 | Realtime Communication | JWT được validate khi connect realtime | **P0** |
| `RC-W5-04` | Tuần 5 | Realtime Communication | Redis Pub/Sub đồng bộ multi-instance | **P1** |
| `RC-W6-02` | Tuần 6 | AI Chatbox & Context Memory | Có cơ chế memory (buffer/summary) | **P0** |
| `RC-W6-04` | Tuần 6 | AI Chatbox & Context Memory | Có endpoint reset/clear session | **P1** |
| `RC-W7-04` | Tuần 7 | RAG (Retrieval-Augmented Generation) | Có pipeline retrieve → LLM → response | **P0** |
| `RC-W7-06` | Tuần 7 | RAG (Retrieval-Augmented Generation) | Kết hợp Memory + RAG | **P1** |

## Chi tiết các Quy tắc cần AI Review

### Tuần 1

#### `RC-W1-10`: Endpoint /login trả access+refresh token

- **Nhóm kỹ năng**: Backend Core & JWT Auth
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận /login thực sự verify password hash và sinh đúng JWT 2 tầng (access + refresh) với payload/expiry hợp lệ — không phải hardcode token giả để pass check.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex_all_of:
  - /login
  - refresh_token
  ```

---

#### `RC-W1-11`: Endpoint /token/refresh

- **Nhóm kỹ năng**: Backend Core & JWT Auth
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận endpoint validate refresh_token (chữ ký, hạn dùng, chưa revoke) TRƯỚC khi cấp access_token mới — không cấp vô điều kiện.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: /token/refresh|/refresh
  ```

---

#### `RC-W1-13`: Có logout / vô hiệu hoá token

- **Nhóm kỹ năng**: Backend Core & JWT Auth
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P1** (Mức độ nghiêm trọng: `high`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận logout thực sự vô hiệu hoá token (xoá refresh_token khỏi DB/Redis hoặc thêm blacklist) — không chỉ trả 200 OK mà không làm gì.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: /logout|blacklist
  ```

---

#### `RC-W1-17`: Password được hash trước khi lưu DB

- **Nhóm kỹ năng**: Backend Core & JWT Auth
- **Loại kiểm tra**: `required_dependency`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận password THỰC SỰ được hash tại điểm gọi trong route /register trước khi insert DB — không lưu plaintext dù có import thư viện hash.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  manifest_glob:
  - '**/requirements.txt'
  - '**/pyproject.toml'
  package_any_of:
  - bcrypt
  - passlib
  - argon2-cffi
  ```

---

### Tuần 3

#### `RC-W3-06`: Cache GET /products với TTL

- **Nhóm kỹ năng**: Database Layer (MySQL + MongoDB + Redis)
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận TTL cụ thể ~30s theo đề bài, và cache áp dụng đúng cho response GET /products (không phải cache 1 endpoint không liên quan để pass check).

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: \.expire\(|setex\(|ttl=
  ```

---

#### `RC-W3-07`: Cache bị xoá khi có CRUD thay đổi dữ liệu

- **Nhóm kỹ năng**: Database Layer (MySQL + MongoDB + Redis)
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận cache bị xoá NGAY khi tạo/sửa/xoá sản phẩm — không phải chỉ hết hạn tự nhiên theo TTL.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: cache\.delete|invalidate_cache|cache_delete
  ```

---

#### `RC-W3-09`: Có job sync định kỳ MySQL → MongoDB

- **Nhóm kỹ năng**: Database Layer (MySQL + MongoDB + Redis)
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận: (a) job chạy ĐỊNH KỲ thật (celery beat/cron/scheduler, không phải hàm gọi tay); (b) dùng timestamp/version để chỉ đồng bộ phần THAY ĐỔI (incremental) — không xoá-ghi-lại toàn bộ mỗi lần chạy.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/workers/**/*.py'
  - '**/services/**/*.py'
  min_matches: 1
  regex: sync
  ```

---

### Tuần 4

#### `RC-W4-05`: Có trang Login + Protected Route

- **Nhóm kỹ năng**: Frontend Next.js 15
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận middleware/guard THỰC SỰ redirect khi chưa đăng nhập — không chỉ được import nhưng chưa gắn vào route nào.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/middleware.ts'
  - '**/middleware.js'
  - '**/*auth*guard*.tsx'
  min_matches: 1
  regex: middleware|NextResponse\.redirect|redirect\(
  ```

---

### Tuần 5

#### `RC-W5-01`: Có SSE hoặc WebSocket, broadcast nhiều user

- **Nhóm kỹ năng**: Realtime Communication
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận broadcast được tới NHIỀU client đồng thời, không phải chỉ echo 1-1 giữa 1 client và server.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: websocket|WebSocket|text/event-stream|EventSourceResponse
  ```

---

#### `RC-W5-02`: Có Connection Manager quản lý nhiều client

- **Nhóm kỹ năng**: Realtime Communication
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận có cấu trúc lưu danh sách connections (dict/list theo room hoặc user) — không phải 1 biến global single-connection.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: ConnectionManager
  ```

---

#### `RC-W5-03`: JWT được validate khi connect realtime

- **Nhóm kỹ năng**: Realtime Communication
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận server TỪ CHỐI kết nối khi token invalid (đóng connection/close code) — không chỉ decode token rồi bỏ qua lỗi nếu decode fail.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: websocket|WebSocket
  ```

---

#### `RC-W5-04`: Redis Pub/Sub đồng bộ multi-instance

- **Nhóm kỹ năng**: Realtime Communication
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P1** (Mức độ nghiêm trọng: `high`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận publish/subscribe dùng CHUNG channel và message thực sự được broadcast tới client ở instance khác — không chỉ khai báo publish/subscribe riêng lẻ không khớp channel.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex_all_of:
  - \.publish\(
  - \.subscribe\(
  ```

---

### Tuần 6

#### `RC-W6-02`: Có cơ chế memory (buffer/summary)

- **Nhóm kỹ năng**: AI Chatbox & Context Memory
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Đây là tiêu chí nặng nhất tuần 6 (25%). Xác nhận context được nối/tóm tắt HỢP LÝ khi hội thoại dài — KHÔNG lặp lại nội dung cũ thừa mỗi lần gọi LLM, không phải chỉ nối chuỗi thô không giới hạn.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: Memory|ConversationBuffer|conversation_summary
  ```

---

#### `RC-W6-04`: Có endpoint reset/clear session

- **Nhóm kỹ năng**: AI Chatbox & Context Memory
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P1** (Mức độ nghiêm trọng: `high`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận endpoint thực sự xoá context đã lưu (DB/Redis) — không chỉ trả 200 OK mà dữ liệu cũ vẫn còn.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: /reset|/clear
  ```

---

### Tuần 7

#### `RC-W7-04`: Có pipeline retrieve → LLM → response

- **Nhóm kỹ năng**: RAG (Retrieval-Augmented Generation)
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P0** (Mức độ nghiêm trọng: `critical`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận kết quả retrieve THỰC SỰ được đưa vào prompt gửi LLM — không phải gọi retriever nhưng bỏ qua kết quả (retrieve xong không dùng).

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/*.py'
  min_matches: 1
  regex: retriev|RAG|RetrievedChunk|search_coding_standard
  ```

---

#### `RC-W7-06`: Kết hợp Memory + RAG

- **Nhóm kỹ năng**: RAG (Retrieval-Augmented Generation)
- **Loại kiểm tra**: `required_code_pattern`
- **Độ ưu tiên**: **P1** (Mức độ nghiêm trọng: `high`)
- **AI Hint (Gợi ý cho AI kiểm tra)**:
  > Xác nhận CẢ HAI (memory hội thoại + RAG retrieval) cùng góp mặt trong 1 lượt trả lời — không phải chỉ dùng 1 trong 2 rồi gọi là hybrid.

- **Cấu trúc / Pattern cần tìm**:
  ```yaml
  glob:
  - '**/ai/**/*.py'
  - '**/rag/**/*.py'
  min_matches: 1
  regex_all_of:
  - Memory|conversation
  - retriev
  ```

---
