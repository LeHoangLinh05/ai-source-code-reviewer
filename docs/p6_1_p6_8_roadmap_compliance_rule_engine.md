# Phase 6.1–6.8 — Roadmap Compliance Rule Engine

Tài liệu này mô tả phần đã hoàn thành cho khối đầu tiên của Phase 6: **Roadmap Compliance Rule Engine**. Đây là lớp kiểm tra deterministic, chạy trước static analysis và AI Agent, dùng để phát hiện các yêu cầu bắt buộc trong roadmap đào tạo bị thiếu khỏi repo.

## Mục tiêu

Roadmap Compliance Rule Engine trả lời câu hỏi: repo có tồn tại bằng chứng file, folder, dependency, config hoặc code pattern cho từng yêu cầu trong roadmap hay không.

Engine này không gọi LLM và không đánh giá đúng/sai logic sâu. Với các rule cần kiểm chứng logic, engine chỉ đánh dấu `provisional_pass` nếu tìm thấy bằng chứng tồn tại, rồi đẩy file liên quan vào `verification_queue` để AI Agent đọc ở phase sau.

## Migration / database contract

Contract roadmap đã có trong Alembic revision:

- `backend/alembic/versions/20260702_0004_roadmap_contract_fields.py`

Revision này:

- Thêm enum `review_issues.category = "requirement"`.
- Thêm enum `review_issues.source = "roadmap_rule"`.
- Cho phép `review_issues.file_path` nullable để issue cấp repo không cần gắn với file cụ thể.
- Thêm `review_reports.compliance_score FLOAT NULL`.
- Thêm `review_reports.bonus_score FLOAT NULL`.

Lưu ý: `TASK.md` đã có `P3.16` tick sẵn từ trước. Online Alembic không verify được trong phiên này vì local PostgreSQL connection timeout, nhưng offline SQL generation cho upgrade/downgrade đã chạy được.

## File chính đã thêm / cập nhật

### `backend/app/ai/rules/roadmap_checker.py`

File chính của engine, gồm:

- Pydantic schema validation cho rule YAML.
- `RoadmapComplianceChecker`.
- Contract output `RoadmapCheckOutput`.
- Result model `RuleResult`.
- Verification queue model `VerificationItem`.
- Logic tính `compliance_score` và `bonus_score`.
- Logic tạo requirement issue khi deterministic fail.
- Method `run()` để chạy thuần deterministic.
- Method `run_and_persist()` để ghi MongoDB và PostgreSQL.

### `backend/app/ai/rules/git_utils.py`

Wrapper riêng cho:

- `git ls-files`
- dùng bởi check type `forbidden_tracked_file`
- mục tiêu chính hiện tại: phát hiện `.env` bị git-track dù file có thể tồn tại local do môi trường dev.

### `backend/app/ai/rules/roadmap_rules_v2.yaml`

Rule set chính thức gồm:

- Tổng cộng `79` rules.
- `40` rule `P0`.
- `26` rule `P1`.
- `13` rule `P2`.
- `16` rule `needs_ai_verification=true`.

Có sửa lại 2 điểm để YAML khớp contract/checker:

- `RC-W2-12`: đổi thành `required_code_pattern` để check URL trong README đúng theo bảng spec.
- `RC-W4-05`: bổ sung regex cho rule `required_code_pattern`, vì trước đó rule có check type code pattern nhưng target chưa có pattern.

### `backend/app/ai/rules/build_rules_yaml.py`

Đây là nguồn sinh YAML. File được cập nhật để:

- Giữ YAML và generator đồng bộ.
- Bổ sung type annotation cho `_rules`.
- Ignore missing stub của PyYAML cho mypy.
- Sinh lại đúng `roadmap_rules_v2.yaml`.

### `backend/app/services/review_pipeline_service.py`

Pipeline được nối thêm bước roadmap:

1. Clone repo.
2. Analyze structure.
3. Chạy `RoadmapComplianceChecker` nếu job có `options.rule_profile`.
4. Chạy static analysis.
5. Merge requirement issues từ roadmap vào danh sách issues cuối.
6. Generate report với `compliance_score` và `bonus_score`.

Nếu `rule_profile` là `null` hoặc không có, engine không chạy.

### `backend/app/workers/review_worker.py`

Worker được bổ sung dependency:

- `RoadmapComplianceResultRepository`
- PostgreSQL session truyền vào pipeline để checker có thể persist roadmap issues.

### `backend/app/repositories/report_repository.py`

Thêm priority override khi sort issues:

- `source=roadmap_rule` luôn được ưu tiên đứng trước issue từ AI/static.
- Có comment rõ rằng các module merge/dedup sau này không được hạ severity hoặc xoá issue `roadmap_rule`.
- Đây là ràng buộc dữ liệu, không chỉ là convention UI.

### `backend/app/services/report_generation_service.py`

Cập nhật report generation để:

- Nhận `compliance_score`.
- Nhận `bonus_score`.
- Xử lý issue có `file_path=None` bằng pseudo path `Repository roadmap requirements` trong `top_risky_files`.
- Ưu tiên group có issue `roadmap_rule` khi build top risky files.

### `backend/app/schemas/normalized_issue.py`

Cho phép:

- `file_path: str | None`

Điều này cần cho requirement issue cấp repo theo contract AI_flow mục 10.1b.

## Check types đã hỗ trợ

Engine hỗ trợ đủ 8 `check_type` theo spec:

- `required_file`
- `required_any_of`
- `required_folder`
- `forbidden_tracked_file`
- `required_dependency`
- `required_code_pattern`
- `min_file_count`
- `required_config_key`

Các check mặc định bỏ qua thư mục nhiễu:

- `.git`
- `node_modules`
- `venv`
- `.venv`
- `__pycache__`
- `dist`
- `build`
- `.next`
- `.mypy_cache`
- `.pytest_cache`
- `.ruff_cache`

## Rule profile contract

Engine chỉ chạy khi job bật profile:

```json
{
  "id": "roadmap_bootcamp_v1",
  "weeks_included": [1, 2]
}
```

Nếu:

- `rule_profile = null` hoặc field không tồn tại: engine không chạy.
- `weeks_included = null` hoặc omit: chạy toàn bộ 79 rules.
- `weeks_included = [1, 2]`: chạy rule tuần 1, tuần 2 và luôn chạy `GEN`.

Filter logic:

```text
rule.week in weeks_included OR rule.week == "GEN"
```

Lưu ý: yêu cầu test ban đầu ghi `weeks_included=[1,2]` chỉ 29 rules, nhưng spec chính ở AI_flow mục 3.1/P6.3 nói `GEN` luôn áp dụng. Vì vậy thực tế đúng contract là:

```text
Tuần 1: 17 rules
Tuần 2: 12 rules
GEN:     5 rules
Tổng:   34 rules
```

## Status rule

Mỗi rule áp dụng trả về một trong 3 status:

- `pass`
- `fail`
- `provisional_pass`

Logic:

- Deterministic fail: `fail`, tạo issue `category=requirement`.
- Deterministic pass + `needs_ai_verification=false`: `pass`.
- Deterministic pass + `needs_ai_verification=true`: `provisional_pass`, không tạo requirement issue, đưa vào `verification_queue`.

## Verification queue

Khi rule cần AI verification pass phần existence, engine tạo item:

```json
{
  "rule_id": "RC-W3-09",
  "file_path": "app/services/sync_service.py",
  "ai_hint": "Xác nhận job chạy định kỳ thật và xử lý incremental theo timestamp..."
}
```

Output này đã sẵn sàng cho Tool 1 / Agent phase sau merge vào danh sách file ưu tiên đọc. Phần merge Agent chưa được code trong phiên này, đúng phạm vi yêu cầu.

## Issue writer

Rule fail tạo issue theo contract:

```text
file_path   = null
category    = requirement
source      = roadmap_rule
confidence  = 1.0
title       = rule.requirement
description = rule.rationale_if_missing
severity    = critical nếu P0, high nếu P1, low nếu P2
raw_output  = { rule_id, week, skill_group, priority, priority_rank }
```

Các issue này là finding deterministic. Agent hoặc dedup module sau này không được:

- xoá issue `source=roadmap_rule`
- hạ severity của issue `source=roadmap_rule`
- coi static/AI cross-check là lý do để loại bỏ roadmap requirement issue

## Scoring

Engine tính:

```text
compliance_score = % rule P0 + P1 pass/provisional_pass
bonus_score      = % rule P2 pass/provisional_pass
```

`provisional_pass` được tính như pass, đúng AI_flow mục 3.5. Nếu Agent phase sau phát hiện logic sai, Agent sẽ tạo issue AI riêng, không sửa lại `compliance_score`.

## MongoDB output

Khi profile bật, engine ghi vào collection:

```text
roadmap_compliance_results
```

Document gồm:

```json
{
  "job_id": "...",
  "rule_profile": { "id": "roadmap_bootcamp_v1", "weeks_included": [1, 2] },
  "checked_at": "...",
  "results": [
    {
      "rule_id": "RC-W5-01",
      "status": "fail",
      "severity": "critical",
      "week": 5,
      "skill_group": "Realtime Communication"
    }
  ],
  "verification_queue": [
    {
      "rule_id": "RC-W1-10",
      "file_path": "app.py",
      "ai_hint": "..."
    }
  ],
  "compliance_score": 71.43,
  "bonus_score": 25.0
}
```

## Tests đã thêm

File:

- `backend/tests/unit/test_roadmap_checker.py`

Các test chính:

- `rule_profile=null` không chạy engine.
- `weeks_included=[1,2]` chỉ áp tuần 1, tuần 2 và `GEN`; không báo thiếu WebSocket/RAG.
- `needs_ai_verification=true` + pattern tìm thấy tạo `provisional_pass`, đưa file vào `verification_queue`, không tạo requirement issue.
- Repo test thiếu WebSocket tạo đúng 1 issue cho `RC-W5-01`, `category=requirement`, `severity=critical`.
- YAML load đúng 79 rules, đúng count priority và đúng 16 rule cần AI verification.

## Validation đã chạy

Các lệnh đã chạy thành công:

```bash
pytest backend\tests\unit
ruff check backend
ruff format --check <các file đã chạm>
mypy <các file đã chạm>
alembic upgrade head --sql
alembic downgrade 20260702_0004:20260701_0003 --sql
alembic downgrade 20260702_0005:20260702_0004 --sql
```

Kết quả:

- Unit tests: `28 passed`.
- Ruff: pass.
- Targeted mypy cho phần đã chạm: pass.
- Offline Alembic SQL generation: pass.

Chưa verify được online:

```bash
alembic current
alembic upgrade head
alembic downgrade ...
```

Lý do: local PostgreSQL connection timeout trên máy chạy hiện tại (`WinError 121`). Đây là lỗi môi trường/DB connection, không phải lỗi import migration.

Full `mypy backend` hiện còn fail ở phần RAG đã dirty trước đó:

- `backend/app/ai/rag/bm25_index.py`
- `backend/app/ai/rag/retriever.py`

Các lỗi này không thuộc khối Roadmap Compliance Rule Engine.

## Checkbox đã hoàn tất

Các task đã tick:

- `P3.16` Roadmap contract migration
- `P6.1` YAML đủ 79 rules + schema validation
- `P6.2` Checker hỗ trợ đủ 8 check type
- `P6.3` Rule profile contract
- `P6.4` Ghi MongoDB roadmap compliance results
- `P6.5` Rule fail ghi thẳng requirement issue
- `P6.6` `needs_ai_verification` tạo `provisional_pass` + queue
- `P6.7` Priority override cho `roadmap_rule`
- `P6.8` Output verification queue sẵn sàng cho Agent phase sau
- `P6.37` Regression test profile null
- `P6.38` Regression test week filter
- `P6.39` Regression test provisional pass

## Những gì chưa làm trong phiên này

Đúng phạm vi yêu cầu, phiên này chưa code:

- AI Agent
- LangChain
- Tool 1 `analyze_project_structure`
- Tool merge `roadmap_verification_queue` vào file priority list
- Agent verify logic thật sự của các `provisional_pass`
- UI roadmap compliance

Các phần trên thuộc khối phase sau.
