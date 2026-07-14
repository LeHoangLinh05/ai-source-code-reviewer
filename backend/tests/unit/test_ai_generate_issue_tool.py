"""Tests for AI issue validation safety gates."""

from pathlib import Path
import importlib
from typing import cast
from uuid import UUID, uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tool_runtime import AIToolRuntime, ai_tool_runtime
from app.ai.issue_verifier import EvidenceVerificationResult
from app.models.review_issue import (
    IssueCategory,
    IssueSeverity,
    IssueSource,
    ReviewIssue,
)
from app.ai.tools.generate_issue import (
    GenerateIssueInput,
    IssueValidationError,
    MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES,
    SourceEvidenceReference,
    _adjust_security_confidence,
    _build_source_context,
    _evidence_rejection_reason,
    _latest_rag_references,
    _latest_kb_grounding,
    _investigation_fields_rejection_reason,
    _optional_category,
    _optional_severity,
    _normalize_issue_file_path,
    _behavior_evidence_owner_rejection_reason,
    _rag_result_reference,
    _sanitize_kb_visible_text,
    validate_issue_payload,
)

generate_issue_module = importlib.import_module("app.ai.tools.generate_issue")


def test_rejects_confidence_below_threshold() -> None:
    with pytest.raises(IssueValidationError, match="confidence"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="medium",
            category="bug",
            title="Possible bug",
            description="The issue is not certain enough.",
            suggestion=None,
            confidence=0.69,
            references=[],
        )


def test_security_issue_requires_knowledge_base_reference(tmp_path: Path) -> None:
    source_path = tmp_path / "auth.py"
    source_path.write_text("validate(token)\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )
    with pytest.raises(IssueValidationError, match="knowledge-base reference"):
        validate_issue_payload(
            file_path=None,
            line_start=None,
            line_end=None,
            severity="high",
            category="security",
            title="Missing validation",
            description="Security finding based on concrete code evidence.",
            suggestion=None,
            confidence=0.75,
            references=[],
        )

    with ai_tool_runtime(runtime):
        validate_issue_payload(
            file_path="auth.py",
            line_start=1,
            line_end=1,
            severity="high",
            category="security",
            title="Missing validation",
            description="Security finding grounded by knowledge.",
            suggestion=None,
            confidence=0.8,
            references=["OWASP ASVS"],
        )


def test_caps_security_confidence_without_references() -> None:
    confidence = _adjust_security_confidence(
        category="security",
        confidence=0.95,
        references=[],
    )

    assert confidence == MAX_SECURITY_CONFIDENCE_WITHOUT_RAG_REFERENCES


def test_issue_enum_helpers_normalize_model_case_drift() -> None:
    assert _optional_severity("High") == "high"
    assert _optional_severity(" CRITICAL ") == "critical"
    assert _optional_category("Security") == "security"
    assert _optional_category(" Maintainability ") == "maintainability"
    assert _optional_severity("P0") == "critical"
    assert _optional_severity("P1") == "high"
    assert _optional_severity("P2") == "low"
    assert _normalize_issue_file_path("./backend\\app/auth.py") == (
        "backend/app/auth.py"
    )


def test_generate_issue_schema_normalizes_location_aliases() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "severity": "critical",
            "category": "security",
            "title": "Plaintext password",
            "description": "Password is stored without hashing.",
            "confidence": 0.8,
            "path": "backend/app/auth.py",
            "line_range": "18-22",
            "source": "KB",
        }
    )

    assert payload.file_path == "backend/app/auth.py"
    assert payload.line_start == 18
    assert payload.line_end == 22


def test_generate_issue_schema_normalizes_evidence_aliases() -> None:
    payload = GenerateIssueInput.model_validate(
        {
            "supporting_evidence": [
                {
                    "file_path": "backend/app/auth.py",
                    "chunk_index": 6,
                    "line_start": 26,
                    "line_end": 38,
                    "description": "The route returns a fixed token.",
                }
            ],
            "contradicting_evidence": "",
        }
    )

    assert payload.supporting_evidence is not None
    assert payload.supporting_evidence[0].rationale == (
        "The route returns a fixed token."
    )
    assert payload.contradicting_evidence == []


def test_missing_investigation_requires_support_and_resolved_contradictions() -> None:
    contradiction = SourceEvidenceReference(
        file_path="app/auth.py",
        chunk_index=1,
        line_start=10,
        line_end=20,
        rationale="The route may implement the required behavior.",
    )

    reason = _investigation_fields_rejection_reason(
        investigation_id="logout-flow",
        claim_type="missing_behavior",
        supporting_evidence=[],
        contradicting_evidence=[contradiction],
        contradiction_resolution=None,
        coverage_summary="Searched route and service owners.",
    )

    assert reason is not None
    assert "supporting_evidence" in reason
    assert "contradiction_resolution" in reason


def test_requirement_issue_requires_runtime_implementation_evidence() -> None:
    verification_harness_evidence = SourceEvidenceReference(
        file_path="backend/verify_logic_errors.py",
        chunk_index=2,
        line_start=62,
        line_end=137,
        rationale="The verification harness demonstrates a login problem.",
    )
    runtime_evidence = SourceEvidenceReference(
        file_path="backend/app/auth.py",
        chunk_index=6,
        line_start=26,
        line_end=38,
        rationale="The login implementation returns fixed tokens.",
    )

    rejected = _behavior_evidence_owner_rejection_reason(
        category="security",
        supporting_evidence=[verification_harness_evidence],
    )
    accepted = _behavior_evidence_owner_rejection_reason(
        category="security",
        supporting_evidence=[verification_harness_evidence, runtime_evidence],
    )

    assert rejected is not None
    assert "runtime implementation" in rejected
    assert accepted is None


def test_confidence_cannot_increase_without_new_evidence(tmp_path: Path) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    assert not runtime.confidence_increase_without_new_evidence(
        fingerprint="same-evidence",
        confidence=0.42,
    )
    assert runtime.confidence_increase_without_new_evidence(
        fingerprint="same-evidence",
        confidence=0.71,
    )


def test_kb_visible_text_removes_internal_rule_identifiers() -> None:
    assert (
        _sanitize_kb_visible_text(
            "Password is plaintext (Rule ID: RC-W1-09). Roadmap violation."
        )
        == "Password is plaintext. requirements violation."
    )


def test_rag_result_reference_prefers_source() -> None:
    assert (
        _rag_result_reference({"source": "OWASP ASVS", "content": "Use hashing."})
        == "OWASP ASVS"
    )


def test_rag_result_reference_falls_back_to_content() -> None:
    assert _rag_result_reference({"content": "Use a password hashing algorithm."}) == (
        "Use a password hashing algorithm."
    )


def test_rag_result_reference_accepts_all_knowledge_documents() -> None:
    assert (
        _rag_result_reference(
            {
                "source": "roadmap_bootcamp_v1",
                "metadata": {"doc_type": "roadmap_rule"},
            }
        )
        == "roadmap_bootcamp_v1"
    )


def test_source_context_contains_nearby_numbered_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "one\ntwo\nproblem\nfour\nfive\n",
        encoding="utf-8",
    )
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        context = _build_source_context(
            file_path="app.py",
            line_start=3,
            line_end=3,
            context_radius=1,
        )

    assert context == {"start_line": 2, "lines": ["two", "problem", "four"]}


@pytest.mark.asyncio
async def test_latest_rag_references_reads_latest_search_trace(tmp_path: Path) -> None:
    job_id = uuid4()
    session_id = uuid4()
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=session_id,
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(
                {
                    "output": {
                        "status": "ok",
                        "results": [
                            {"source": "OWASP ASVS", "content": "Use hashing."},
                            {"content": "Store passwords with a slow hash."},
                        ],
                    }
                }
            ),
        ),
    )

    with ai_tool_runtime(runtime):
        references = await _latest_rag_references()

    assert references == ["OWASP ASVS", "Store passwords with a slow hash."]


@pytest.mark.asyncio
async def test_latest_rag_references_accepts_roadmap_catalog_trace(
    tmp_path: Path,
) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(
                {
                    "tool_name": "roadmap_rule_catalog",
                    "output": {
                        "status": "ok",
                        "results": [],
                    },
                }
            ),
        ),
    )

    with ai_tool_runtime(runtime):
        references = await _latest_rag_references()

    assert references == ["roadmap_rule_catalog"]


@pytest.mark.asyncio
async def test_kb_grounding_accepts_structure_checklist(tmp_path: Path) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _StructureGroundingDatabase(),
        ),
    )

    with ai_tool_runtime(runtime):
        references, metadata = await _latest_kb_grounding()

    assert references == ["knowledge_base checklist"]
    assert metadata == {"knowledge_doc_type": "roadmap_rule"}


@pytest.mark.asyncio
async def test_kb_grounding_selects_requested_rule_instead_of_first_result(
    tmp_path: Path,
) -> None:
    document: dict[str, object] = {
        "output": {
            "status": "ok",
            "results": [
                {
                    "source": "wrong-rule",
                    "metadata": {
                        "doc_type": "roadmap_rule",
                        "rule_id": "RC-W1-04",
                    },
                },
                {
                    "source": "logout-rule",
                    "metadata": {
                        "doc_type": "roadmap_rule",
                        "rule_id": "RC-W1-13",
                        "priority": "P0",
                    },
                },
            ],
        }
    }
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _FakeMongoDatabase(document),
        ),
    )

    with ai_tool_runtime(runtime):
        references, metadata = await _latest_kb_grounding("RC-W1-13")

    assert references == ["logout-rule"]
    assert metadata == {
        "knowledge_doc_type": "roadmap_rule",
        "rule_id": "RC-W1-13",
        "priority": "P0",
    }


def test_accepts_requirement_category_for_structural_kb_issue(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with ai_tool_runtime(runtime):
        validate_issue_payload(
            file_path="pyproject.toml",
            line_start=1,
            line_end=1,
            severity="high",
            category="requirement",
            title="Required dependency is missing",
            description="The required runtime dependency is not declared.",
            suggestion=None,
            confidence=1.0,
            references=["Project requirements"],
        )


class _FakeMongoDatabase:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.collection = _FakeMongoCollection(document)

    def __getitem__(self, _name: str) -> "_FakeMongoCollection":
        return self.collection


class _FakeMongoCollection:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document

    async def find_one(
        self,
        _query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> dict[str, object] | None:
        _ = sort
        return self.document


class _StructureGroundingDatabase:
    def __getitem__(self, _name: str) -> "_StructureGroundingCollection":
        return _StructureGroundingCollection()


class _StructureGroundingCollection:
    async def find_one(
        self,
        query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> dict[str, object] | None:
        _ = sort
        if query.get("tool_name") == "search_knowledge_base":
            return None
        return {"output": {"roadmap": {"applicable_rule_ids": ["RC-W1-17"]}}}


def test_rejects_line_range_that_does_not_exist(tmp_path: Path) -> None:
    source_path = tmp_path / "app.py"
    source_path.write_text("print('hello')\n", encoding="utf-8")
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with (
        ai_tool_runtime(runtime),
        pytest.raises(
            IssueValidationError,
            match="does not exist",
        ),
    ):
        validate_issue_payload(
            file_path="app.py",
            line_start=2,
            line_end=2,
            severity="medium",
            category="bug",
            title="Invalid line",
            description="The line does not exist in the file.",
            suggestion=None,
            confidence=0.8,
            references=[],
        )


def test_rejects_file_path_that_does_not_exist(tmp_path: Path) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(AsyncIOMotorDatabase, object()),
    )

    with (
        ai_tool_runtime(runtime),
        pytest.raises(
            IssueValidationError,
            match="File does not exist in sandbox",
        ),
    ):
        validate_issue_payload(
            file_path="backend/websocket.py",
            line_start=1,
            line_end=1,
            severity="high",
            category="bug",
            title="Invented path",
            description="The model must not invent file paths.",
            suggestion=None,
            confidence=0.8,
            references=[],
        )


@pytest.mark.asyncio
async def test_generate_issue_does_not_persist_verifier_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, session = _build_generate_issue_runtime(tmp_path)

    async def reject_candidate(
        _candidate: object,
        _evidence: object,
    ) -> EvidenceVerificationResult:
        return EvidenceVerificationResult(
            verdict="reject",
            reason="Contradicting source implements the behavior.",
            confidence_cap=0.1,
            severity_cap="info",
            contradictions=["Implementation is present."],
        )

    monkeypatch.setattr(
        generate_issue_module,
        "verify_issue_candidate",
        reject_candidate,
    )

    with ai_tool_runtime(runtime):
        response = await generate_issue_module.generate_issue.ainvoke(
            _generate_issue_payload()
        )

    assert response["status"] == "rejected"
    assert "independent verifier" in str(response["reason"])
    assert session.added == []


@pytest.mark.asyncio
async def test_generate_issue_persists_verifier_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, session = _build_generate_issue_runtime(tmp_path)

    async def accept_candidate(
        _candidate: object,
        _evidence: object,
    ) -> EvidenceVerificationResult:
        return EvidenceVerificationResult(
            verdict="accept",
            reason="Source directly proves the defect.",
            confidence_cap=0.78,
            severity_cap="medium",
        )

    monkeypatch.setattr(
        generate_issue_module,
        "verify_issue_candidate",
        accept_candidate,
    )

    with ai_tool_runtime(runtime):
        response = await generate_issue_module.generate_issue.ainvoke(
            _generate_issue_payload()
        )

    assert response["status"] == "created"
    assert len(session.added) == 1
    issue = session.added[0]
    assert issue.confidence == 0.78
    assert issue.severity.value == "medium"
    assert issue.raw_output is not None
    assert issue.raw_output["verification"] == {
        "verdict": "accept",
        "reason": "Source directly proves the defect.",
        "confidence_cap": 0.78,
        "severity_cap": "medium",
        "unsupported_claims": [],
        "contradictions": [],
    }


@pytest.mark.asyncio
async def test_incomplete_generate_issue_skips_duplicate_evidence(
    tmp_path: Path,
) -> None:
    runtime, session = _build_generate_issue_runtime(tmp_path)
    existing_issue = ReviewIssue(
        job_id=runtime.job_id,
        file_path="service.py",
        line_start=1,
        line_end=2,
        severity=IssueSeverity.HIGH,
        category=IssueCategory.BUG,
        title="Authentication returns a fixed token",
        description="Every caller receives the same token.",
        suggestion=None,
        source=IssueSource.AI_REVIEW,
        confidence=0.9,
        raw_output=None,
    )
    existing_issue.id = uuid4()
    session.existing_issue = existing_issue

    incomplete_payload = {
        "investigation_id": "fixed-token",
        "claim_type": "present_defect",
        "supporting_evidence": [
            {
                "tool_sequence": 3,
                "file_path": "service.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 2,
                "rationale": "The function returns a literal token.",
            }
        ],
        "coverage_summary": "Read the complete authentication function.",
    }

    with ai_tool_runtime(runtime):
        response = await generate_issue_module.generate_issue.ainvoke(
            incomplete_payload,
        )

    assert response == {
        "status": "skipped",
        "reason": "duplicate_ai_review_issue_from_supporting_evidence",
        "issue_id": str(existing_issue.id),
        "source": "ai_review",
    }
    assert session.added == []


@pytest.mark.asyncio
async def test_claimed_source_range_can_span_adjacent_supporting_chunks(
    tmp_path: Path,
) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _EvidenceMongoDatabase(
                [
                    {
                        "tool_name": "read_file_chunk",
                        "sequence": 14,
                        "output": {
                            "status": "ok",
                            "file_path": "backend/app/auth.py",
                            "chunk_index": 5,
                            "line_start": 23,
                            "line_end": 25,
                        },
                    },
                    {
                        "tool_name": "read_file_chunk",
                        "sequence": 6,
                        "output": {
                            "status": "ok",
                            "file_path": "backend/app/auth.py",
                            "chunk_index": 6,
                            "line_start": 26,
                            "line_end": 38,
                        },
                    },
                ]
            ),
        ),
    )
    supporting_evidence = [
        SourceEvidenceReference(
            file_path="backend/app/auth.py",
            chunk_index=6,
            line_start=26,
            line_end=38,
            rationale="The handler returns hardcoded tokens.",
        ),
        SourceEvidenceReference(
            file_path="backend/app/auth.py",
            chunk_index=5,
            line_start=23,
            line_end=25,
            rationale="The route decorator defines the /login endpoint.",
        ),
    ]

    with ai_tool_runtime(runtime):
        reason = await _evidence_rejection_reason(
            investigation_id="login",
            claim_type="present_defect",
            file_path="backend/app/auth.py",
            line_start=23,
            line_end=38,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=[],
        )

    assert reason is None


@pytest.mark.asyncio
async def test_disjoint_claimed_source_range_lists_supported_ranges(
    tmp_path: Path,
) -> None:
    runtime = AIToolRuntime(
        job_id=uuid4(),
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, object()),
        mongodb_database=cast(
            AsyncIOMotorDatabase,
            _EvidenceMongoDatabase(
                [
                    {
                        "tool_name": "read_file_chunk",
                        "sequence": 8,
                        "output": {
                            "status": "ok",
                            "file_path": "backend/app/auth.py",
                            "chunk_index": 5,
                            "line_start": 23,
                            "line_end": 25,
                        },
                    },
                    {
                        "tool_name": "read_file_chunk",
                        "sequence": 20,
                        "output": {
                            "status": "ok",
                            "file_path": "backend/app/auth.py",
                            "chunk_index": 8,
                            "line_start": 42,
                            "line_end": 48,
                        },
                    },
                ]
            ),
        ),
    )
    supporting_evidence = [
        SourceEvidenceReference(
            file_path="backend/app/auth.py",
            chunk_index=8,
            line_start=42,
            line_end=48,
            rationale="Refresh token is not validated.",
        ),
        SourceEvidenceReference(
            file_path="backend/app/auth.py",
            chunk_index=5,
            line_start=23,
            line_end=25,
            rationale="Login route context.",
        ),
    ]

    with ai_tool_runtime(runtime):
        reason = await _evidence_rejection_reason(
            investigation_id="jwt-refresh",
            claim_type="present_defect",
            file_path="backend/app/auth.py",
            line_start=23,
            line_end=48,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=[],
        )

    assert reason is not None
    assert "backend/app/auth.py:23-25" in reason
    assert "backend/app/auth.py:42-48" in reason
    assert "Use one contiguous covered range per issue" in reason


def _build_generate_issue_runtime(
    tmp_path: Path,
) -> tuple[AIToolRuntime, "_GenerateIssueSession"]:
    (tmp_path / "service.py").write_text(
        "def authenticate(user):\n    return 'fake-token'\n",
        encoding="utf-8",
    )
    job_id = uuid4()
    session = _GenerateIssueSession(job_id)
    database = _EvidenceMongoDatabase(
        {
            "tool_name": "read_file_chunk",
            "sequence": 3,
            "output": {
                "status": "ok",
                "file_path": "service.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 2,
            },
        }
    )
    runtime = AIToolRuntime(
        job_id=job_id,
        session_id=uuid4(),
        sandbox_path=tmp_path,
        postgres_session=cast(AsyncSession, session),
        mongodb_database=cast(AsyncIOMotorDatabase, database),
    )
    return runtime, session


def _generate_issue_payload() -> dict[str, object]:
    return {
        "severity": "critical",
        "category": "bug",
        "title": "Authentication returns a fixed token",
        "description": "Every caller receives the same token.",
        "confidence": 0.95,
        "file_path": "service.py",
        "line_start": 1,
        "line_end": 2,
        "source": "ai_review",
        "investigation_id": "fixed-token",
        "claim_type": "present_defect",
        "supporting_evidence": [
            {
                "tool_sequence": 3,
                "file_path": "service.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 2,
                "rationale": "The function returns a literal token.",
            }
        ],
        "contradicting_evidence": [],
        "coverage_summary": "Read the complete authentication function.",
    }


class _GenerateIssueResult:
    def __init__(
        self,
        job_id: UUID,
        existing_issue: ReviewIssue | None,
    ) -> None:
        self.job_id = job_id
        self.existing_issue = existing_issue

    def scalar_one_or_none(self) -> UUID:
        return self.job_id

    def scalars(self) -> "_GenerateIssueResult":
        return self

    def first(self) -> ReviewIssue | None:
        return self.existing_issue


class _GenerateIssueSession:
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        self.added: list[ReviewIssue] = []
        self.existing_issue: ReviewIssue | None = None

    async def execute(self, _statement: object) -> _GenerateIssueResult:
        return _GenerateIssueResult(self.job_id, self.existing_issue)

    def add(self, issue: ReviewIssue) -> None:
        self.added.append(issue)

    async def commit(self) -> None:
        return None

    async def refresh(self, issue: ReviewIssue) -> None:
        if getattr(issue, "id", None) is None:
            issue.id = uuid4()


class _EvidenceCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    async def to_list(self, *, length: object) -> list[dict[str, object]]:
        _ = length
        return self.documents


class _EvidenceCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def find(self, _query: dict[str, object]) -> _EvidenceCursor:
        return _EvidenceCursor(self.documents)

    async def find_one(
        self,
        _query: dict[str, object],
        *,
        sort: list[tuple[str, int]],
    ) -> None:
        _ = sort
        return None


class _EvidenceMongoDatabase:
    def __init__(self, documents: dict[str, object] | list[dict[str, object]]) -> None:
        if isinstance(documents, dict):
            documents = [documents]
        self.collection = _EvidenceCollection(documents)

    def __getitem__(self, _name: str) -> _EvidenceCollection:
        return self.collection
