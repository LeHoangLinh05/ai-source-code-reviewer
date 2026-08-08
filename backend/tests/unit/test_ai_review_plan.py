"""Tests for AI chunk review plan selection."""

from app.ai.review.plan import (
    REVIEW_MODE_FULL_AUDIT,
    REVIEW_MODE_SMART,
    build_chunk_review_plan,
    expected_chunk_keys_from_plan,
    get_review_mode,
    get_smart_review_max_chunks,
)


def test_review_mode_defaults_to_full_audit() -> None:
    assert get_review_mode(None) == REVIEW_MODE_FULL_AUDIT
    assert get_review_mode({"run_static_analysis": True}) == REVIEW_MODE_FULL_AUDIT


def test_review_mode_allows_explicit_smart_review() -> None:
    assert get_review_mode({"review_mode": "smart"}) == REVIEW_MODE_SMART


def test_smart_review_plan_ignores_static_issue_flags() -> None:
    documents = [
        _chunk("app/auth/routes.py", 0, total_chunks=2, risk_area="security"),
        _chunk("app/auth/routes.py", 1, total_chunks=2, risk_area="security"),
        _chunk("app/service.py", 0, has_static_issues=True),
        _chunk("app/service.py", 1),
        _chunk("docs/readme.md", 0, language="markdown"),
    ]
    with_static = build_chunk_review_plan(
        chunk_documents=documents,
        review_mode=REVIEW_MODE_SMART,
        max_smart_chunks=3,
    )
    documents[2]["has_static_issues"] = False
    without_static = build_chunk_review_plan(
        chunk_documents=documents,
        review_mode=REVIEW_MODE_SMART,
        max_smart_chunks=3,
    )

    assert with_static == without_static
    assert with_static["mode"] == REVIEW_MODE_SMART
    assert with_static["target_chunks"] == 3
    assert expected_chunk_keys_from_plan(with_static) == {
        ("app/auth/routes.py", 0),
        ("app/auth/routes.py", 1),
        ("app/service.py", 0),
    }
    assert with_static["total_available_chunks"] == 5


def test_full_audit_review_plan_targets_all_chunks() -> None:
    plan = build_chunk_review_plan(
        chunk_documents=[
            _chunk("app/a.py", 0, total_chunks=2),
            _chunk("app/a.py", 1, total_chunks=2),
            _chunk("README.md", 0, language="markdown"),
        ],
        review_mode=REVIEW_MODE_FULL_AUDIT,
    )

    assert plan["target_chunks"] == 3
    assert expected_chunk_keys_from_plan(plan) == {
        ("app/a.py", 0),
        ("app/a.py", 1),
        ("README.md", 0),
    }


def test_smart_review_max_chunks_is_bounded() -> None:
    assert get_smart_review_max_chunks({"smart_review_max_chunks": 5}) == 120
    assert get_smart_review_max_chunks({"smart_review_max_chunks": 800}) == 240


def test_smart_review_plan_audits_all_chunks_for_small_repositories() -> None:
    plan = build_chunk_review_plan(
        chunk_documents=[
            _chunk("app/a.py", 0, total_chunks=3),
            _chunk("app/a.py", 1, total_chunks=3),
            _chunk("app/a.py", 2, total_chunks=3),
            _chunk("app/b.py", 0),
        ],
        review_mode=REVIEW_MODE_SMART,
        max_smart_chunks=10,
    )

    assert plan["target_chunks"] == 4
    assert expected_chunk_keys_from_plan(plan) == {
        ("app/a.py", 0),
        ("app/a.py", 1),
        ("app/a.py", 2),
        ("app/b.py", 0),
    }


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
