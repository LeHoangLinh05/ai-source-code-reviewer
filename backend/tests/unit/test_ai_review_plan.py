"""Tests for AI chunk review plan selection."""

from app.ai.review_plan import (
    REVIEW_MODE_FULL_AUDIT,
    REVIEW_MODE_SMART,
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)


def test_review_mode_defaults_to_smart() -> None:
    assert get_review_mode(None) == REVIEW_MODE_SMART
    assert get_review_mode({"run_static_analysis": True}) == REVIEW_MODE_SMART


def test_smart_review_plan_targets_risk_and_static_chunks() -> None:
    plan = build_chunk_review_plan(
        chunk_documents=[
            _chunk("app/auth/routes.py", 0, total_chunks=2, risk_area="security"),
            _chunk("app/auth/routes.py", 1, total_chunks=2, risk_area="security"),
            _chunk("app/service.py", 0, has_static_issues=True),
            _chunk("app/service.py", 1),
            _chunk("docs/readme.md", 0, language="markdown"),
        ],
        review_mode=REVIEW_MODE_SMART,
        verification_queue=[],
        max_smart_chunks=3,
    )

    assert plan["mode"] == REVIEW_MODE_SMART
    assert plan["target_chunks"] == 3
    assert expected_chunk_keys_from_plan(plan) == {
        ("app/auth/routes.py", 0),
        ("app/auth/routes.py", 1),
        ("app/service.py", 0),
    }
    assert plan["total_available_chunks"] == 5


def test_full_audit_review_plan_targets_all_chunks() -> None:
    plan = build_chunk_review_plan(
        chunk_documents=[
            _chunk("app/a.py", 0, total_chunks=2),
            _chunk("app/a.py", 1, total_chunks=2),
            _chunk("README.md", 0, language="markdown"),
        ],
        review_mode=REVIEW_MODE_FULL_AUDIT,
        verification_queue=[],
    )

    assert plan["target_chunks"] == 3
    assert expected_chunk_keys_from_plan(plan) == {
        ("app/a.py", 0),
        ("app/a.py", 1),
        ("README.md", 0),
    }


def test_smart_review_max_chunks_is_bounded() -> None:
    assert get_smart_review_max_chunks({"smart_review_max_chunks": 5}) == 20
    assert get_smart_review_max_chunks({"smart_review_max_chunks": 800}) == 500


def test_roadmap_verification_adds_related_implementation_context() -> None:
    plan = build_chunk_review_plan(
        chunk_documents=[
            _chunk(
                "requirements.txt",
                0,
                language="text",
                chunk_text="fastapi\nbcrypt\n",
            ),
            _chunk(
                "app/auth/routes.py",
                0,
                risk_area="security",
                function_name="register",
                chunk_text=(
                    "async def register(payload):\n"
                    "    hashed_password = hash_password(payload.password)\n"
                    "    await repository.create_user(hashed_password)\n"
                ),
            ),
            _chunk("app/reports.py", 0, chunk_text="def export_report(): pass"),
        ],
        review_mode=REVIEW_MODE_SMART,
        verification_queue=[
            {
                "rule_id": "RC-W1-17",
                "file_path": "requirements.txt",
                "ai_hint": "Xác nhận password THỰC SỰ được hash tại route /register.",
            }
        ],
        max_smart_chunks=20,
    )

    expected = expected_chunk_keys_from_plan(plan)

    assert ("requirements.txt", 0) in expected
    assert ("app/auth/routes.py", 0) in expected
    files = {str(file_plan["file_path"]): file_plan for file_plan in plan["files"]}
    auth_verifications = files["app/auth/routes.py"]["roadmap_verifications"]
    assert auth_verifications == [
        {
            "rule_id": "RC-W1-17",
            "ai_hint": "Xác nhận password THỰC SỰ được hash tại route /register.",
            "match_type": "related_context",
        }
    ]


def _chunk(
    file_path: str,
    chunk_index: int,
    *,
    total_chunks: int = 1,
    risk_area: str = "general",
    language: str = "python",
    function_name: str | None = None,
    chunk_text: str = "",
    has_static_issues: bool = False,
) -> dict[str, object]:
    return {
        "file_path": file_path,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "risk_area": risk_area,
        "language": language,
        "function_name": function_name,
        "chunk_text": chunk_text,
        "has_static_issues": has_static_issues,
    }
