"""Regression tests for independent AI issue evidence verification."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

import app.ai.issue_verifier as issue_verifier
from app.ai.issue_verifier import (
    EvidenceChunk,
    EvidenceVerificationResult,
    IssueCandidate,
    cap_severity,
    load_evidence_chunk,
    verify_issue_candidate,
)
from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime


class _FakeStructuredLLM:
    def __init__(self, result: EvidenceVerificationResult) -> None:
        self.result = result
        self.prompt = ""

    async def ainvoke(self, prompt: str) -> EvidenceVerificationResult:
        self.prompt = prompt
        return self.result


class _FakeLLM:
    def __init__(self, result: EvidenceVerificationResult) -> None:
        self.structured = _FakeStructuredLLM(result)

    def with_structured_output(
        self,
        _schema: type[EvidenceVerificationResult],
    ) -> _FakeStructuredLLM:
        return self.structured


def _candidate(
    *,
    title: str = "Required behavior is missing",
    claim_type: Literal["present_defect", "missing_behavior"] = "missing_behavior",
) -> IssueCandidate:
    return IssueCandidate(
        claim_type=claim_type,
        title=title,
        description="The target behavior is absent from the system.",
        suggestion="Implement the behavior in its owning layer.",
        category="security",
        severity="critical",
        confidence=0.92,
        file_path="app/service.py",
        line_start=1,
        line_end=2,
        coverage_summary="Checked route and service owners.",
        contradiction_resolution="The candidate claimed the route was unrelated.",
    )


def _evidence(
    content: str,
    *,
    role: Literal["supporting", "contradicting"] = "contradicting",
) -> EvidenceChunk:
    return EvidenceChunk(
        role=role,
        file_path="app/auth.py",
        chunk_index=0,
        line_start=1,
        line_end=4,
        rationale="This owner implements the behavior under review.",
        content=content,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "source"),
    [
        ("Chat service lacks logout", '@router.post("/logout")\nrevoke_token(token)'),
        (
            "Auth service stores plaintext passwords",
            "hash_password(password)\nverify_password(raw, hashed)",
        ),
        (
            "Authentication login is missing",
            '@router.post("/login")\nreturn create_access_token(user.id)',
        ),
        (
            "Token refresh is missing",
            '@router.post("/refresh")\nrotate_refresh_token(token)',
        ),
    ],
)
async def test_verifier_rejects_false_positive_regressions(
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    source: str,
) -> None:
    verdict = EvidenceVerificationResult(
        verdict="reject",
        reason="Contradicting source implements the claimed missing behavior.",
        confidence_cap=0.1,
        severity_cap="info",
        contradictions=["The implementation is present in an owning layer."],
    )
    fake_llm = _FakeLLM(verdict)

    async def run_call(
        call: Callable[[object], Awaitable[object]],
        *,
        allow_fallback: bool,
    ) -> object:
        assert allow_fallback
        return await call(fake_llm)

    monkeypatch.setattr(issue_verifier, "run_with_configured_llm", run_call)
    monkeypatch.setattr(
        issue_verifier,
        "get_settings",
        lambda: type("Settings", (), {"enable_ai_issue_verifier": True})(),
    )

    result = await verify_issue_candidate(_candidate(title=title), [_evidence(source)])

    assert result.verdict == "reject"
    assert title in fake_llm.structured.prompt
    assert json.dumps(source)[1:-1] in fake_llm.structured.prompt
    assert "Absence from one file is not proof" in fake_llm.structured.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        '@router.post("/login")\nreturn {"access_token": "fake-token"}',
        "def logout(token):\n    return True  # token remains valid",
    ],
)
async def test_verifier_accepts_directly_proven_defects(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    verdict = EvidenceVerificationResult(
        verdict="accept",
        reason="The source directly demonstrates the defective behavior.",
        confidence_cap=0.82,
        severity_cap="high",
    )
    fake_llm = _FakeLLM(verdict)

    async def run_call(
        call: Callable[[object], Awaitable[object]],
        *,
        allow_fallback: bool,
    ) -> object:
        assert allow_fallback
        return await call(fake_llm)

    monkeypatch.setattr(issue_verifier, "run_with_configured_llm", run_call)
    monkeypatch.setattr(
        issue_verifier,
        "get_settings",
        lambda: type("Settings", (), {"enable_ai_issue_verifier": True})(),
    )

    result = await verify_issue_candidate(
        _candidate(title="Authentication behavior is defective"),
        [_evidence(source, role="supporting")],
    )

    assert result.verdict == "accept"
    assert result.confidence_cap == 0.82


@pytest.mark.asyncio
async def test_verifier_skips_llm_when_job_budget_is_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_run_call(
        call: Callable[[object], Awaitable[object]],
        *,
        allow_fallback: bool,
    ) -> object:
        _ = call, allow_fallback
        raise AssertionError("verifier should not call the LLM")

    monkeypatch.setattr(issue_verifier, "run_with_configured_llm", fail_run_call)
    monkeypatch.setattr(issue_verifier, "get_remaining_llm_call_budget", lambda: 2)
    monkeypatch.setattr(
        issue_verifier,
        "get_settings",
        lambda: type("Settings", (), {"enable_ai_issue_verifier": True})(),
    )

    with pytest.raises(issue_verifier.IssueVerificationError, match="budget"):
        await verify_issue_candidate(
            _candidate(title="Authentication behavior is defective"),
            [_evidence('return {"access_token": "fake-token"}', role="supporting")],
        )


def test_load_evidence_chunk_reads_full_numbered_range(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        evidence = load_evidence_chunk(
            role="supporting",
            file_path="service.py",
            chunk_index=0,
            line_start=2,
            line_end=3,
            rationale="Relevant implementation.",
        )

    assert evidence.content == "2: two\n3: three"


def test_severity_cap_never_raises_requested_severity() -> None:
    assert cap_severity("critical", "medium") == "medium"
    assert cap_severity("low", "critical") == "low"
