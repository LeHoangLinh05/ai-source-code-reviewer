# WBS — RepoGuard AI
# Copy từng dòng vào Excel theo cột: Category | Task | Subtask | ET (h) | Status | Start date | End date

---

## DÁN VÀO EXCEL — FORMAT BẢNG

| Category | Task | Subtask | ET (h) | Status |
|----------|------|---------|--------|--------|
| Phân tích nghiệp vụ & Lập kế hoạch | Phân tích bài toán, xác định vấn đề và đối tượng sử dụng | Xác định vấn đề cần giải quyết (code review thủ công tốn thời gian, thiếu reviewer) và đối tượng sử dụng (Developer, Team Lead, Student, Security Engineer) | | Done |
| | | Xác định phạm vi MVP (10 tính năng bắt buộc: Auth, Clone repo, Static analysis, AI review, Report) và phân loại ưu tiên P0/P1/P2 | | Done |
| | | Lựa chọn công nghệ: FastAPI + Next.js 15 + PostgreSQL + MongoDB + Redis + Celery + LangChain + ChromaDB và lý do chọn từng công nghệ | | Done |
| | Phân rã công việc (WBS) và lập timeline 28 ngày | Phân rã dự án thành ~135 subtask theo 6 phase, ước lượng thời gian (ET) cho từng task, gán ngày bắt đầu/kết thúc trong 28 ngày | | In Progress |
| Thiết kế hệ thống & cơ sở dữ liệu — Vẽ kiến trúc tổng thể và thiết kế database trước khi code | Thiết kế sơ đồ database (ERD) cho PostgreSQL | Vẽ sơ đồ tổng quan các thành phần hệ thống (System Architecture diagram) và review với mentor | 4 | In Progress |
| | | Thiết kế ERD (Entity Relationship Diagram): xác định các bảng Users, Repositories, ReviewJobs, Reports, Issues và mối quan hệ giữa chúng | 3 | To Do |
| | Thiết kế kiến trúc hệ thống và luồng xử lý review | Xác định kiến trúc tổng thể: Client (Next.js) → Nginx → FastAPI → Celery Worker → AI Agent, kèm các database PostgreSQL, MongoDB, Redis, ChromaDB | 3 | Done |
| | | Vẽ flowchart luồng xử lý từ khi user nhập URL → clone repo → phân tích → AI review → trả report | 2 | Done |
| Xác thực người dùng — Đăng ký, đăng nhập và phân quyền truy cập hệ thống | Trang đăng nhập (UI + API xác thực bằng JWT) | Thiết kế giao diện trang đăng nhập (form email/password, nút đăng nhập, link sang đăng ký) | 4 | To Do |
| | | Xây dựng API backend xử lý đăng nhập: nhận email + password, xác thực, trả về JWT access token và refresh token | 4 | To Do |
| | Trang đăng ký tài khoản (UI + API lưu user) | Thiết kế giao diện trang đăng ký (form email, mật khẩu, xác nhận mật khẩu) | 3 | To Do |
| | | Xây dựng API backend xử lý đăng ký: validate input, hash password bằng bcrypt, lưu user vào PostgreSQL | 2 | To Do |
| | Phân quyền truy cập theo vai trò (User / Admin) | Xây dựng middleware phân quyền: user thường chỉ xem dữ liệu của mình, admin xem được tất cả — dùng JWT claims + decorator | 4 | To Do |
| Quản lý kho mã nguồn — Thêm, xem và quản lý các repository GitHub/GitLab cần review | Thêm repository mới bằng URL GitHub/GitLab | Thiết kế giao diện form nhập URL repository (hỗ trợ GitHub, GitLab public repos) với validation URL | 3 | To Do |
| | | Xây dựng API nhận URL repository từ user, kiểm tra URL hợp lệ (chỉ chấp nhận github.com, gitlab.com), lưu vào PostgreSQL | 3 | To Do |
| | Xem danh sách tất cả repository đã thêm | Thiết kế giao diện hiển thị tất cả repository đã thêm của user (dạng cards/table: tên repo, URL, lần review gần nhất) | 3 | To Do |
| | | Xây dựng API trả về danh sách repository của user hiện tại, kèm thông tin lần review gần nhất | 2 | To Do |
| | Xem chi tiết 1 repository và lịch sử review | Thiết kế giao diện xem chi tiết 1 repository: thông tin repo + danh sách tất cả các lần review đã chạy | 3 | To Do |
| | | Xây dựng API trả về thông tin chi tiết repository và lịch sử các review job đã thực hiện trên repo đó | 3 | To Do |
| Quản lý phiên review code — Khởi tạo, theo dõi trạng thái và xử lý các lần review trên repository | Khởi tạo phiên review mới trên 1 repository | Thiết kế giao diện cho user bấm "Bắt đầu Review": chọn branch, ngôn ngữ, tuỳ chọn bật/tắt static analysis | 3 | To Do |
| | | Xây dựng API tạo phiên review mới: ghi record vào PostgreSQL (status=PENDING), đẩy task vào hàng đợi Redis để Celery Worker xử lý nền | 4 | To Do |
| | Xem danh sách tất cả các lần review đã chạy | Thiết kế giao diện hiển thị tất cả các lần review của user (bảng có cột: repo, ngày tạo, trạng thái, điểm số) | 3 | To Do |
| | | Xây dựng API trả về danh sách review job của user, hỗ trợ phân trang và lọc theo trạng thái (Pending/Running/Done/Failed) | 2 | To Do |
| | Clone mã nguồn về server và quản lý sandbox | Xây dựng service tải mã nguồn từ GitHub/GitLab về server (git clone --depth 1), lưu vào thư mục tạm riêng biệt cho mỗi review job | 5 | To Do |
| | | Xây dựng cơ chế giới hạn dung lượng repo tối đa 100MB, tự động xoá thư mục tạm sau 1 giờ để tránh đầy ổ đĩa | 3 | To Do |
| Phân tích mã nguồn tự động — Quét lỗi bằng công cụ static analysis và chuẩn bị dữ liệu cho AI | Nhận diện cấu trúc project (ngôn ngữ, framework, file tree) | Quét thư mục repo đã clone để nhận diện ngôn ngữ lập trình (Python, JS/TS) và framework (FastAPI, React, Express) | 4 | To Do |
| | | Tạo cây thư mục dự án, loại bỏ file không cần review (node_modules, .git, ảnh, binary) và xếp hạng ưu tiên file cần review | 3 | To Do |
| | Chạy công cụ quét lỗi tự động (ruff, bandit, eslint) | Chạy công cụ quét lỗi tự động cho Python: ruff (linting, style) + bandit (bảo mật), thu thập kết quả dạng JSON | 5 | To Do |
| | | Chạy công cụ quét lỗi tự động cho JavaScript/TypeScript: eslint, thu thập kết quả dạng JSON | 4 | To Do |
| | | Chuẩn hoá kết quả từ tất cả công cụ về cùng một định dạng chung (NormalizedIssue): file, dòng, mức nghiêm trọng, mô tả, gợi ý sửa | 3 | To Do |
| | Chia nhỏ code thành chunk để AI đọc được | Chia nhỏ source code thành từng đoạn (chunk) theo ranh giới hàm/class để vừa context window của AI (tối đa 1500 tokens/chunk) | 4 | To Do |
| Pipeline AI review code — AI Agent đọc code, phát hiện lỗi, đánh giá chất lượng và sinh báo cáo | Xây dựng AI Agent tự động review code (ReAct + Tool Calling) | Xây dựng AI Agent (dùng LangChain + Gemini/GPT) hoạt động theo mô hình ReAct: AI tự lên kế hoạch → gọi tool → phân tích → sinh kết quả | 8 | To Do |
| | | Xây dựng các tool cho Agent sử dụng: đọc chunk code, tra cứu knowledge base, tạo issue có cấu trúc (file, dòng, severity, gợi ý sửa) | 6 | To Do |
| | | Đưa kết quả static analysis (ruff, bandit) vào prompt của AI Agent để AI validate và bổ sung thêm lỗi logic mà tool không phát hiện được | 3 | To Do |
| | Tổng hợp báo cáo và tính điểm chất lượng code | Gộp toàn bộ issue từ static analysis + AI review, loại bỏ trùng lặp, tính điểm chất lượng (Security, Maintainability, Performance, Overall) | 5 | To Do |
| | | Sử dụng AI sinh bản tóm tắt tổng quan (executive summary) mô tả tình trạng code và các vấn đề ưu tiên cần sửa | 4 | To Do |
| Cơ sở tri thức & RAG — Lưu trữ tài liệu chuẩn coding và truy vấn để AI review có căn cứ | Thu thập tài liệu chuẩn và nạp vào vector database | Thu thập tài liệu chuẩn: OWASP Top 10 (bảo mật web), PEP8 (Python style), Clean Code principles → chuyển sang dạng markdown | 3 | To Do |
| | | Chia tài liệu thành chunks 512 tokens, tạo embedding vectors và lưu vào ChromaDB (vector database chạy local) | 4 | To Do |
| | Tra cứu tài liệu liên quan khi AI review code | Xây dựng chức năng tìm kiếm: khi AI gặp lỗi bảo mật → tự động tra cứu tài liệu OWASP liên quan để có căn cứ khi đưa ra nhận xét | 4 | To Do |
| | | Kết nối RAG vào AI Agent: Agent tự động gọi search knowledge base trước khi flag issue bảo mật → giảm nhận xét sai (hallucination) | 3 | To Do |
| Báo cáo & Dashboard — Hiển thị kết quả review: điểm số, biểu đồ, danh sách lỗi và so sánh giữa các lần review | Trang tổng quan kết quả review (điểm số + biểu đồ) | Thiết kế giao diện dashboard kết quả review: điểm tổng thể, điểm từng mảng (Security/Maintainability/Performance), biểu đồ phân bố lỗi | 6 | To Do |
| | | Xây dựng API trả về dữ liệu report: tổng số lỗi theo severity, theo category, top file nhiều lỗi nhất, bản tóm tắt AI | 4 | To Do |
| | Bảng danh sách lỗi có lọc và tìm kiếm | Thiết kế giao diện bảng danh sách lỗi: có bộ lọc theo mức nghiêm trọng (Critical/High/Medium/Low), theo loại (Security/Bug/Style), có tìm kiếm | 5 | To Do |
| | | Xây dựng API trả về danh sách lỗi với filter theo severity, category, file path — hỗ trợ phân trang (20 issue/trang) | 3 | To Do |
| | Xem chi tiết 1 lỗi kèm đoạn code bị lỗi | Thiết kế giao diện xem chi tiết 1 lỗi: mô tả lỗi, đoạn code bị lỗi (syntax highlight), gợi ý cách sửa, nguồn phát hiện (AI hay static tool) | 4 | To Do |
| | | Xây dựng API trả về thông tin chi tiết từng issue: vị trí file + dòng, mô tả, gợi ý sửa, confidence score | 2 | To Do |
| | So sánh kết quả giữa 2 lần review trên cùng repo | Thiết kế giao diện so sánh 2 lần review trên cùng 1 repo: lỗi nào đã sửa, lỗi nào còn, lỗi nào mới phát sinh | 3 | To Do |
| | | Xây dựng API so sánh 2 review job: tính diff số lượng issue, liệt kê issue mới/đã sửa/còn lại | 3 | To Do |
| Theo dõi tiến trình realtime — Hiển thị trạng thái review trực tiếp trên giao diện khi job đang chạy | Xây dựng backend SSE đẩy trạng thái review về frontend | Xây dựng endpoint SSE (Server-Sent Events): server đẩy trạng thái review về frontend theo thời gian thực (VD: "Đang clone 10%", "AI đang review 65%") | 5 | To Do |
| | | Xây dựng cầu nối Redis Pub/Sub → SSE: Celery Worker publish trạng thái vào Redis channel, FastAPI subscribe và stream về client | 4 | To Do |
| | Thiết kế giao diện thanh tiến trình theo từng bước | Thiết kế giao diện thanh tiến trình dạng bước (step indicator): Cloning → Analyzing → Static Analysis → AI Review → Report → Done | 5 | To Do |
| Gỡ lỗi AI & Giám sát — Trang debug hiển thị chi tiết cách AI Agent suy luận và tra cứu tài liệu | Xem lại quá trình AI suy luận (timeline tool calls) | Thiết kế giao diện xem lại quá trình AI làm việc: hiển thị timeline các tool call (đọc file nào, search RAG query gì, sinh issue gì) theo thứ tự | 4 | To Do |
| | | Xây dựng API trả về trace log của AI Agent từ MongoDB: danh sách tool calls, input/output mỗi lần gọi, thời gian xử lý | 3 | To Do |
| | Xem kết quả tra cứu knowledge base (RAG results) | Thiết kế giao diện xem kết quả tra cứu knowledge base: hiển thị query → top 3 tài liệu được truy xuất + điểm similarity | 3 | To Do |
| Đóng gói & Triển khai Docker — Container hoá toàn bộ hệ thống để chạy bằng một lệnh docker-compose up | Viết Dockerfile và Docker Compose cho toàn bộ stack | Viết Dockerfile cho backend (Python 3.12 + cài ruff, bandit) và frontend (Node 20, multi-stage build) | 3 | To Do |
| | | Viết docker-compose.yml kết nối tất cả services: PostgreSQL, MongoDB, Redis, Backend, Celery Worker, Frontend, Nginx | 4 | To Do |
| | Cấu hình Nginx reverse proxy cho API và SSE | Cấu hình Nginx làm reverse proxy: điều hướng /api → backend, / → frontend, tắt buffering để SSE hoạt động | 2 | To Do |
| | Viết script khởi tạo dữ liệu và cấu hình ban đầu | Viết script khởi tạo dữ liệu ban đầu: ingest tài liệu OWASP/PEP8 vào ChromaDB, tạo tài khoản admin mặc định | 2 | To Do |
