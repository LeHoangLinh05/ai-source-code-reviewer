Nhìn vào file kế hoạch của bạn thì **phương pháp chuẩn nhất không phải “Naive RAG”**. Dự án của bạn là **AI Source Code Reviewer cấp repository**, có static analyzer, AI agent, tool calling, RAG coding standards, report issue có line number, severity, confidence, evidence các thứ. Tức là nó không phải chatbot hỏi đáp tài liệu bình thường nữa. Nếu vẫn làm kiểu “chunk code → vector search → hỏi LLM” thì hơi phí công thiết kế cả cái hệ thống, như mua dao mổ tim về gọt xoài.

## Kết luận trước

Với RepoGuard AI, nên chọn:

```text id="3a40p2"
Agentic Hybrid RAG
+ Metadata Filtering
+ Parent-Child Retrieval
+ Static Analysis Grounding
+ Evidence Mapping
+ Structured Output
```

Nhưng nói chính xác hơn:

> **RAG trong dự án của bạn nên dùng chủ yếu cho “coding standards / security guidelines”, còn source code thì nên xử lý bằng static analysis + AST chunking + metadata retrieval, không nên phụ thuộc hoàn toàn vào vector search.**

Trong tài liệu của bạn, hệ thống đã có clone repo, phân tích cấu trúc project, chạy ruff/bandit/eslint, chunk code, AI agent review, RAG với OWASP/Python best practices, tool calling, confidence threshold và line validation. Đây chính là nền rất hợp để làm kiểu **Agentic RAG có kiểm soát**, không phải RAG đơn giản. 

---

# 1. Vì sao không nên chọn Naive RAG?

Naive RAG là:

```text id="d45tx6"
Code → chia chunk → embedding → search top-k → đưa cho LLM → sinh issue
```

Nghe đơn giản, nhưng với source code thì có vấn đề:

```text id="3zfsm7"
- Code cần line number chính xác
- Tên hàm, tên class, import, file path rất quan trọng
- Một lỗi có thể nằm ở nhiều file
- Vector search dễ lấy đoạn “na ná đúng”
- Review code cần bằng chứng, không chỉ “AI cảm thấy có lỗi”
```

Ví dụ lỗi auth có thể nằm rải ở:

```text id="1i8g2v"
auth/router.py
auth/service.py
core/security.py
models/user.py
middleware/auth.py
```

Nếu chỉ vector search, nó có thể lấy thiếu context. Xong LLM phán “có thể có lỗi bảo mật”, nghe rất tự tin, rất sai, rất đúng tinh thần AI demo hỏng.

---

# 2. Phương pháp chuẩn nhất cho dự án của bạn

## Tên phương pháp nên ghi trong tài liệu

Bạn có thể đặt tên phần này là:

```text id="9deukb"
Evidence-Grounded Agentic Hybrid RAG for Repository-Level Code Review
```

Dịch dễ hiểu:

```text id="xfx9d5"
RAG lai có agent điều phối, dựa trên bằng chứng, dùng cho review source code cấp repository.
```

Nghe đủ học thuật, đủ kỹ thuật, và không quá “bốc phét công nghệ”.

---

# 3. Kiến trúc RAG nên dùng

## 3.1. Không dùng một kho RAG duy nhất

Bạn nên tách thành **2 loại retrieval**:

```text id="fwwv1k"
1. Code Retrieval
   → tìm file/chunk code liên quan trong repository

2. Knowledge Retrieval
   → tìm coding standards, OWASP, best practices
```

Đây là điểm rất quan trọng.

---

## 3.2. Code Retrieval: dùng metadata + AST, không chỉ vector

Với source code, cách tốt nhất là:

```text id="xucbho"
AST-based Chunking
+ Metadata Filtering
+ Parent-Child Retrieval
```

Tức là:

```text id="98uxi2"
File code
→ parse AST
→ tách theo class/function
→ lưu metadata: file_path, language, function_name, class_name, imports, line_start, line_end
→ khi review thì lấy chunk nhỏ
→ nhưng đưa thêm parent context như cả class/cả file liên quan
```

Trong tài liệu của bạn cũng đã định hướng chunk code theo ranh giới ngữ nghĩa: class boundary, function boundary, block boundary, fallback fixed lines với overlap. Đây là lựa chọn đúng cho code review. 

Ví dụ metadata nên lưu:

```json id="49rtg6"
{
  "file_path": "app/auth/utils.py",
  "language": "python",
  "chunk_type": "function",
  "function_name": "verify_token",
  "line_start": 42,
  "line_end": 67,
  "imports": ["jwt", "datetime"],
  "module": "auth",
  "risk_area": "security"
}
```

Khi AI review auth, nó không nên search toàn repo. Nó nên filter:

```text id="fjaomy"
module = auth
language = python
risk_area = security
```

Rồi mới retrieve.

---

## 3.3. Knowledge Retrieval: dùng Hybrid RAG

Với OWASP, PEP8, FastAPI best practices, Clean Code, security checklist, bạn nên dùng:

```text id="1q8av8"
Hybrid RAG = Vector Search + Keyword Search
```

Tài liệu của bạn đang chọn ChromaDB + embedding `all-MiniLM-L6-v2`, chạy local, có metadata filtering. Cái này ổn cho MVP. 

Nhưng nếu muốn “chuẩn” hơn, nên bổ sung keyword search đơn giản bằng BM25 hoặc full-text search.

Vì sao?

OWASP/security docs có nhiều keyword chính xác:

```text id="me4i55"
SQL injection
XSS
CSRF
JWT
hardcoded secret
insecure deserialization
parameterized query
```

Vector search hiểu nghĩa tốt, nhưng keyword search bắt thuật ngữ tốt hơn. Kết hợp cả hai thì retrieval đỡ ngu, một thành tựu không nhỏ.

---

# 4. Flow chuẩn nên dùng cho RepoGuard AI

Đây là flow tôi khuyên bạn dùng:

```text id="elutyk"
1. Clone repo
2. Detect language/framework/file tree
3. Chạy static analysis: ruff, bandit, eslint
4. Chunk code theo AST/function/class
5. Gắn metadata cho từng chunk
6. Chọn file/chunk cần review theo priority
7. Agent lập kế hoạch review
8. Với mỗi issue nghi ngờ:
   - đọc code chunk
   - lấy parent context nếu cần
   - search coding standard/OWASP bằng Hybrid RAG
   - cross-check với static analysis
   - validate line number
9. Sinh issue dạng JSON
10. Deduplicate issue
11. Generate report + score
12. Lưu evidence + confidence
```

Nói gọn:

```text id="s2gxpv"
Static tools phát hiện lỗi rõ ràng.
AI phát hiện lỗi logic/ngữ cảnh.
RAG cung cấp chuẩn tham chiếu.
Metadata giúp tìm đúng code.
Evidence mapping giúp report đáng tin.
```

Đây mới là “phương pháp chuẩn” cho bài của bạn.

---

# 5. Vai trò của từng phương pháp trong dự án

| Thành phần           | Nên dùng                           | Mục đích                                                    |
| -------------------- | ---------------------------------- | ----------------------------------------------------------- |
| Source code          | AST Chunking + Metadata Filtering  | Tách code đúng theo function/class                          |
| Tìm code liên quan   | Parent-Child Retrieval             | Lấy chunk nhỏ nhưng vẫn có context lớn                      |
| Coding standards     | Hybrid RAG                         | Tìm OWASP, best practices, guideline                        |
| Review workflow      | Agentic RAG                        | Agent tự gọi tool đọc file, search standard, generate issue |
| Giảm hallucination   | Evidence Mapping + Line Validation | Issue phải có file, line, evidence                          |
| Static bugs/security | Static Analysis Grounding          | Ruff/Bandit/ESLint làm nguồn kiểm chứng                     |
| Output               | Structured JSON                    | Dễ lưu DB, filter UI, export report                         |

---

# 6. Cái nào nên làm trong 4 tuần?

Bạn đang có deadline 4 tuần, nên đừng tham Graph RAG full dependency graph ngay. Đó là con đường biến đồ án thành khảo cổ học tinh thần.

## MVP nên làm

```text id="rzv5ka"
1. AST-based code chunking
2. Metadata filtering
3. ChromaDB vector RAG cho coding standards
4. Static analysis grounding
5. Agent tool calling
6. Structured issue JSON
7. Evidence mapping
```

## Advanced nhưng đáng làm

```text id="bp5hzl"
1. Hybrid RAG: thêm keyword/BM25 search
2. Parent-child retrieval
3. RAG debug page
4. Issue deduplication
5. Compare two review reports
```

## Chưa nên làm ngay

```text id="ukf3y9"
1. Full Graph RAG
2. Repo-wide call graph phức tạp
3. Multi-agent quá nhiều agent
4. Private repo OAuth
5. Semgrep nếu setup bị nặng
```

---

# 7. Bạn nên mô tả phương pháp trong báo cáo như này

Bạn có thể ghi:

```text id="ozukg2"
RepoGuard AI sử dụng phương pháp Evidence-Grounded Agentic Hybrid RAG cho bài toán review source code cấp repository.

Khác với Naive RAG chỉ truy xuất top-k chunks bằng vector similarity, hệ thống tách riêng hai loại retrieval:

(1) Code Retrieval: source code được phân tích bằng AST, chia theo function/class boundary, gắn metadata như file_path, language, module, line_start, line_end. Khi review, agent dùng metadata filtering và parent-child retrieval để lấy đúng đoạn code cùng ngữ cảnh cha.

(2) Knowledge Retrieval: coding standards, OWASP Top 10, Python/FastAPI best practices được lưu trong vector database. Khi phát hiện issue, agent truy xuất guideline liên quan để kiểm chứng và làm căn cứ sinh suggestion.

Ngoài ra, hệ thống kết hợp static analysis tools như Ruff, Bandit và ESLint để ground kết quả AI review, giảm hallucination và giảm false positive. Mỗi issue sinh ra bắt buộc có severity, category, file path, line number, confidence score và evidence/reference.
```

Đoạn này đưa vào SRS hoặc slide là ổn. Không màu mè quá, nhưng nghe có phương pháp thật.

---

# 8. Mapping với tài liệu hiện tại của bạn

Trong file của bạn đã có sẵn mấy phần rất hợp:

```text id="ccz0r8"
- AI Agent dùng ReAct pattern
- Tool calling: analyze_project_structure, read_file_chunk, search_coding_standard, generate_issue, generate_final_report
- RAG với OWASP + Python best practices
- ChromaDB local
- Code chunking bằng AST
- Static analyzer: ruff, bandit, eslint
- Confidence threshold >= 0.7
- Line number validation
- Structured issues JSON
- AI debug page hiển thị tool calls và RAG results
```

Nên bạn không cần đổi hướng lớn. Chỉ cần **định danh phương pháp rõ hơn** và sửa lại RAG Design cho chặt hơn.

---

# 9. Nên sửa phần RAG Design như thế nào?

Hiện phần RAG của bạn đang hơi nghiêng về:

```text id="0f8b0m"
RAG = search coding standards
```

Cái đó đúng, nhưng hơi hẹp. Nên sửa thành:

```text id="e3d18d"
RAG trong RepoGuard gồm 2 lớp:
1. Repository Context Retrieval
2. Coding Standard Retrieval
```

Cấu trúc đề xuất:

```text id="4g3hz4"
7. RAG DESIGN

7.1 RAG Method Selection
- Method: Evidence-Grounded Agentic Hybrid RAG
- Reason:
  + Source code cần truy xuất theo metadata, AST, line number
  + Security review cần grounding bằng OWASP/best practices
  + Agent cần gọi tool linh hoạt theo từng loại issue
  + Static analysis dùng để kiểm chứng và giảm hallucination

7.2 Repository Context Retrieval
- AST-based chunking
- Metadata filtering
- Parent-child context expansion
- Static issue injection

7.3 Coding Standard Retrieval
- Vector search bằng ChromaDB
- Optional keyword/BM25 search
- Metadata filtering theo language/doc_type/category
- Top-k retrieval + similarity score

7.4 Agentic Review Flow
- Agent plan review
- Read file chunk
- Search coding standard nếu phát hiện security/performance issue
- Generate structured issue
- Validate line number
- Deduplicate with static issues
- Generate final report

7.5 Anti-Hallucination
- Confidence threshold
- Static tool cross-check
- Evidence mapping
- Line validation
- RAG reference required for security issues
```

---

# 10. Chọn “chuẩn nhất” theo mức độ mentor đánh giá

Nếu mentor hỏi:

> “Dự án của em dùng RAG kiểu gì?”

Bạn trả lời như này là đẹp:

```text id="dcr57w"
Dự án của em không dùng Naive RAG đơn thuần. Em dùng Evidence-Grounded Agentic Hybrid RAG.

Với source code, em không chỉ vector hóa toàn bộ rồi search top-k, vì code cần line number, function boundary và metadata chính xác. Em chunk code bằng AST theo function/class, lưu metadata như file_path, language, function_name, line_start, line_end. Khi AI review, agent dùng tool đọc chunk và mở rộng context theo parent file/class nếu cần.

Với coding standards, em dùng RAG để retrieve OWASP, Python/FastAPI best practices và security checklist. Trước khi AI flag security issue, agent phải search guideline liên quan để làm căn cứ.

Ngoài ra, kết quả AI được cross-check với static analyzers như Ruff, Bandit, ESLint. Issue chỉ được lưu nếu có structured output, confidence >= 0.7, line number hợp lệ và evidence/reference rõ ràng.
```

Câu này đủ “có phương pháp”, không bị nói là chỉ gắn AI vào cho có.

---

# 11. Kết luận cuối cùng

Dự án của bạn nên chọn:

```text id="jx9cqx"
Evidence-Grounded Agentic Hybrid RAG
```

Cụ thể triển khai:

```text id="dymxcq"
Code:
AST Chunking + Metadata Filtering + Parent-Child Retrieval

Knowledge Base:
Hybrid RAG với OWASP/Python/FastAPI/Clean Code

Agent:
ReAct Tool Calling để đọc file, search standard, sinh issue

Validation:
Static Analysis + Line Validation + Confidence Threshold + Evidence Mapping
```

Đây là phương án cân bằng nhất giữa:

```text id="e0ps66"
- đủ sâu để mentor không chê dễ
- đủ thực tế để làm được trong 4 tuần
- đúng bản chất code review
- không tự sát bằng Graph RAG full dependency trong MVP
```

Nói thẳng: **đừng biến RAG thành trung tâm tuyệt đối của dự án**. Trung tâm của RepoGuard nên là **review pipeline**. RAG chỉ là một phần giúp AI có căn cứ khi đánh giá code. Đặt đúng vai trò như vậy thì dự án sẽ chắc hơn nhiều.
