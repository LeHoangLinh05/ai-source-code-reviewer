"""Tests for review pipeline configuration helpers."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.ai.roadmap.knowledge import ROADMAP_PROFILE_ID
from app.analyzers.file_filter import FileManifest
from app.models.review_job import ReviewJobStatus
from app.schemas.repo_summary import RepoSummary
from app.services.code_indexing.documents import (
    build_plain_file_chunk_metadata,
    build_plain_file_chunk_metadata_documents,
)
from app.services.code_indexing.service import CodeIndexingService
from app.services.review_pipeline import service as review_pipeline_service
from app.services.review_pipeline.service import (
    ReviewJobCanceled,
    ReviewPipelineError,
    ReviewPipelineService,
    StructureAnalysisResult,
    get_rule_profile,
)


def test_get_rule_profile_is_disabled_when_options_missing() -> None:
    assert get_rule_profile(None) is None


def test_get_rule_profile_is_disabled_when_key_missing() -> None:
    assert get_rule_profile({"run_static_analysis": True}) is None


def test_get_rule_profile_preserves_explicit_profile() -> None:
    rule_profile = {"id": ROADMAP_PROFILE_ID, "weeks_included": [1, 2]}

    assert get_rule_profile({"rule_profile": rule_profile}) == rule_profile


def test_get_rule_profile_rejects_invalid_profile_shape() -> None:
    with pytest.raises(ReviewPipelineError, match="rule_profile option"):
        get_rule_profile({"rule_profile": "roadmap_bootcamp_v1"})


def test_plain_text_files_are_chunked_for_ai_coverage(tmp_path: Path) -> None:
    source_path = tmp_path / "frontend" / "app.tsx"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "export function App() {\n  return null\n}\n", encoding="utf-8"
    )

    metadata = build_plain_file_chunk_metadata(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=source_path,
        issues=[],
    )

    assert metadata.file_path == "frontend/app.tsx"
    assert metadata.chunk_index == 0
    assert metadata.total_chunks == 1
    assert metadata.chunk_text == "export function App() {\n  return null\n}"


def test_large_plain_text_files_are_split_for_embedding_limits(tmp_path: Path) -> None:
    source_path = tmp_path / "backend" / "app" / "rules.yaml"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "\n".join(
            f"rule_{line_number}: "
            + " ".join(f"requirement_{index}" for index in range(40))
            for line_number in range(120)
        ),
        encoding="utf-8",
    )

    chunks = build_plain_file_chunk_metadata_documents(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=source_path,
        issues=[],
    )

    assert len(chunks) > 1
    assert {chunk.file_path for chunk in chunks} == {"backend/app/rules.yaml"}
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.total_chunks == len(chunks) for chunk in chunks)
    assert all(chunk.token_count <= 1500 for chunk in chunks)


def test_markdown_plain_chunks_skip_non_readme_files(tmp_path: Path) -> None:
    docs_path = tmp_path / "docs" / "guide.md"
    docs_path.parent.mkdir(parents=True)
    docs_path.write_text("# Guide\n\nDetails\n", encoding="utf-8")

    chunks = build_plain_file_chunk_metadata_documents(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=docs_path,
        issues=[],
    )

    assert chunks == []


def test_markdown_plain_chunks_keep_readme_files(tmp_path: Path) -> None:
    readme_path = tmp_path / "README.md"
    readme_path.write_text("# Project\n\nSetup instructions\n", encoding="utf-8")

    chunks = build_plain_file_chunk_metadata_documents(
        job_id=uuid4(),
        sandbox_path=tmp_path,
        file_path=readme_path,
        issues=[],
    )

    assert len(chunks) == 1
    assert chunks[0].file_path == "README.md"
    assert chunks[0].language == "markdown"


@pytest.mark.asyncio
async def test_chunk_code_persists_cache_metadata_and_indexes_repo_branch(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app" / "service.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def create_user():\n    return True\n", encoding="utf-8")
    repository_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        repository_id=repository_id,
        branch="feature/cache",
        commit_sha="abc123",
        repository=SimpleNamespace(default_branch="main"),
    )
    code_store = _RecordingCodeStore()
    chunk_repository = _RecordingChunkRepository()
    manifest_repository = _RecordingManifestRepository()
    trace_repository = _RecordingTraceRepository()
    service = CodeIndexingService(
        settings=cast(
            Any,
            SimpleNamespace(
                mongodb_chunk_batch_size=500,
                code_embedding_provider="mistral",
                code_embedding_model="codestral-embed-2505",
                code_embedding_dimension=1536,
            ),
        ),
        chunk_repository=cast(Any, chunk_repository),
        manifest_repository=cast(Any, manifest_repository),
        trace_repository=cast(Any, trace_repository),
        embedding_store=cast(Any, code_store),
    )

    await service.index(
        review_job=cast(Any, job),
        sandbox_path=tmp_path,
        filtered_files=[source_path],
        issues=[],
    )

    assert len(chunk_repository.documents) == 1
    document = chunk_repository.documents[0]
    assert document.repository_id == repository_id
    assert document.branch == "feature/cache"
    assert document.commit_sha == "abc123"
    assert document.content_hash is not None
    assert document.embedding_cache_id is not None
    assert document.repo_branch_key is not None
    assert document.index_generation_key is not None
    assert [manifest.status for manifest in manifest_repository.documents] == [
        "BUILDING",
        "INDEXED",
    ]
    assert len(trace_repository.documents) == 1
    assert code_store.index_calls == [
        {
            "chunk_count": 1,
            "repository_id": repository_id,
            "branch": "feature/cache",
            "commit_sha": "abc123",
        }
    ]


@pytest.mark.asyncio
async def test_pipeline_delegates_chunking_to_code_indexing_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app" / "service.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def create_user():\n    return True\n", encoding="utf-8")
    job = SimpleNamespace(id=uuid4())
    code_indexing_service = _RecordingCodeIndexingService()
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.code_indexing_service = code_indexing_service

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(service, "_transition", noop)
    monkeypatch.setattr(service, "_publish_status", noop)

    await service._chunk_code(job, tmp_path, [source_path], [])

    assert code_indexing_service.calls == [
        {
            "review_job": job,
            "sandbox_path": tmp_path,
            "filtered_files": [source_path],
            "issues": [],
        }
    ]


@pytest.mark.asyncio
async def test_chunk_code_publishes_progress_after_indexing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = SimpleNamespace(id=uuid4())
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.code_indexing_service = _RecordingCodeIndexingService()
    transitions: list[ReviewJobStatus] = []
    published_messages: list[str] = []

    async def transition(
        _review_job: object,
        status: ReviewJobStatus,
        _progress: int,
        _message: str,
    ) -> None:
        transitions.append(status)

    async def publish_status(
        _review_job: object,
        status: ReviewJobStatus,
        _progress: int,
        message: str,
    ) -> None:
        transitions.append(status)
        published_messages.append(message)

    monkeypatch.setattr(service, "_transition", transition)
    monkeypatch.setattr(service, "_publish_status", publish_status)

    await service._chunk_code(job, tmp_path, [], [])

    assert transitions == [
        ReviewJobStatus.CHUNKING_CODE,
        ReviewJobStatus.CHUNKING_CODE,
    ]
    assert published_messages == ["Source chunks ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_pipeline_keeps_code_embedding_cache_for_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
) -> None:
    job_id = uuid4()
    code_store = _RecordingCodeStore()
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.settings = SimpleNamespace(sandbox_root=str(tmp_path))
    service.review_job_repository = _ExistingJobRepository()
    service.code_embedding_store = code_store

    async def run_pipeline_steps(_review_job: object, _sandbox_path: Path) -> None:
        if outcome == "failed":
            raise RuntimeError("pipeline failed")
        if outcome == "cancelled":
            raise ReviewJobCanceled

    async def handle_failure(_job_id: object, _error: Exception) -> None:
        return None

    monkeypatch.setattr(service, "_run_pipeline_steps", run_pipeline_steps)
    monkeypatch.setattr(service, "_handle_failure", handle_failure)

    await service.run(job_id)

    assert code_store.deleted_job_ids == []


@pytest.mark.asyncio
async def test_pipeline_runs_repo_summary_between_structure_and_static(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        repository_id=uuid4(),
        options=None,
        commit_sha=None,
    )
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.settings = SimpleNamespace(
        max_repo_size_mb=500,
        max_source_file_size_bytes=1_048_576,
    )
    service.review_job_repository = _ExistingJobRepository()
    step_order: list[str] = []

    async def transition(
        _review_job: object,
        status: ReviewJobStatus,
        _progress: int,
        _message: str,
    ) -> None:
        step_order.append(status.value)

    async def analyze_structure(
        _review_job: object,
        _sandbox_path: Path,
        _filtered_files: list[Path],
    ) -> StructureAnalysisResult:
        step_order.append("ANALYZE_METHOD")
        return StructureAnalysisResult(project_structure={}, file_tree=[])

    async def generate_repo_summary(_review_job: object, _sandbox_path: Path) -> None:
        step_order.append(ReviewJobStatus.GENERATING_SUMMARY.value)

    async def run_static_analysis(
        _review_job: object,
        _sandbox_path: Path,
        _filtered_files: list[Path],
    ) -> list[object]:
        step_order.append(ReviewJobStatus.RUNNING_STATIC_ANALYSIS.value)
        return []

    async def noop_async(*_args: object, **_kwargs: object) -> None:
        return None

    async def chunk_code(*_args: object, **_kwargs: object) -> None:
        step_order.append(ReviewJobStatus.CHUNKING_CODE.value)

    monkeypatch.setattr(
        review_pipeline_service, "clone_repository", lambda *_args: None
    )
    monkeypatch.setattr(
        review_pipeline_service, "validate_repo_size", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        review_pipeline_service, "get_commit_sha", lambda _sandbox_path: "abc123"
    )
    monkeypatch.setattr(
        review_pipeline_service,
        "build_file_manifest",
        lambda *_args, **_kwargs: FileManifest(
            files=[], source="test", scanned_count=0
        ),
    )
    monkeypatch.setattr(review_pipeline_service, "scan_secrets", lambda *_args: [])
    monkeypatch.setattr(
        review_pipeline_service, "attach_source_context", lambda *_args: None
    )
    monkeypatch.setattr(service, "_transition", transition)
    monkeypatch.setattr(service, "_analyze_structure", analyze_structure)
    monkeypatch.setattr(service, "_generate_repo_summary", generate_repo_summary)
    monkeypatch.setattr(service, "_run_static_analysis", run_static_analysis)
    monkeypatch.setattr(service, "_chunk_code", chunk_code)
    monkeypatch.setattr(service, "_persist_pre_agent_report", noop_async)
    monkeypatch.setattr(service, "_run_ai_agent", noop_async)
    monkeypatch.setattr(service, "_require_ai_generated_report", noop_async)
    monkeypatch.setattr(service, "_publish_status", noop_async)

    await service._run_pipeline_steps(job, tmp_path)

    assert step_order[:5] == [
        ReviewJobStatus.CLONING.value,
        "ANALYZE_METHOD",
        ReviewJobStatus.GENERATING_SUMMARY.value,
        ReviewJobStatus.RUNNING_STATIC_ANALYSIS.value,
        ReviewJobStatus.CHUNKING_CODE.value,
    ]


@pytest.mark.asyncio
async def test_generate_repo_summary_persists_document_and_publishes_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        repository_id=uuid4(),
        commit_sha="abc123",
    )
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.settings = SimpleNamespace(openai_model="gpt-4o-mini")
    service.review_job_repository = _ExistingJobRepository()
    service.repo_summary_repository = _RecordingRepoSummaryRepository()
    published_events: list[tuple[object, str, dict[str, object]]] = []

    async def publish_job_progress(
        job_id: object,
        event_type: str,
        payload: dict[str, object],
    ) -> int:
        published_events.append((job_id, event_type, payload))
        return 1

    monkeypatch.setattr(
        review_pipeline_service,
        "RepoSummaryService",
        lambda: _SuccessfulRepoSummaryService(),
    )
    monkeypatch.setattr(
        review_pipeline_service,
        "publish_job_progress",
        publish_job_progress,
    )

    await service._generate_repo_summary(job, tmp_path)

    assert len(service.repo_summary_repository.documents) == 1
    document = service.repo_summary_repository.documents[0]
    assert document.repository_id == job.repository_id
    assert document.job_id == job.id
    assert document.commit_sha == "abc123"
    assert document.model_used == "gpt-4o-mini"
    assert document.purpose == "A tiny test API."
    assert published_events == [
        (
            job.id,
            "status_change",
            {
                "status": ReviewJobStatus.GENERATING_SUMMARY.value,
                "progress": 50,
                "message": "Generating repository summary",
            },
        ),
        (
            job.id,
            "status_change",
            {
                "status": ReviewJobStatus.GENERATING_SUMMARY.value,
                "progress": 55,
                "message": "Repository summary generated",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_generate_repo_summary_failure_is_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = SimpleNamespace(id=uuid4(), repository_id=uuid4(), commit_sha="abc123")
    service: Any = ReviewPipelineService.__new__(ReviewPipelineService)
    service.settings = SimpleNamespace(openai_model="gpt-4o-mini")
    service.review_job_repository = _ExistingJobRepository()
    service.repo_summary_repository = _RecordingRepoSummaryRepository()
    published_messages: list[str] = []

    async def publish_job_progress(
        _job_id: object,
        _event_type: str,
        payload: dict[str, object],
    ) -> int:
        published_messages.append(str(payload["message"]))
        return 1

    monkeypatch.setattr(
        review_pipeline_service,
        "RepoSummaryService",
        lambda: _FailingRepoSummaryService(),
    )
    monkeypatch.setattr(
        review_pipeline_service,
        "publish_job_progress",
        publish_job_progress,
    )

    await service._generate_repo_summary(job, tmp_path)

    assert service.repo_summary_repository.documents == []
    assert published_messages == [
        "Generating repository summary",
        "Repository summary generation failed; continuing review",
    ]


class _ExistingJobRepository:
    async def get_by_id(self, job_id: object) -> object:
        return SimpleNamespace(id=job_id)

    async def mark_started(self, review_job: object, *, sandbox_path: str) -> None:
        _ = review_job, sandbox_path

    async def update_clone_metadata(
        self,
        review_job: Any,
        *,
        commit_sha: str,
    ) -> Any:
        review_job.commit_sha = commit_sha
        return review_job

    async def update_status(
        self,
        review_job: Any,
        *,
        status: ReviewJobStatus,
        message: str,
        progress: int,
    ) -> Any:
        review_job.status = status
        review_job.message = message
        review_job.progress = progress
        return review_job


class _RecordingCodeStore:
    def __init__(self) -> None:
        self.deleted_job_ids: list[str] = []
        self.index_calls: list[dict[str, object]] = []

    def index_chunks(
        self,
        chunks: list[object],
        *,
        repository_id: object = None,
        branch: str | None = None,
        commit_sha: str | None = None,
    ) -> object:
        self.index_calls.append(
            {
                "chunk_count": len(chunks),
                "repository_id": repository_id,
                "branch": branch,
                "commit_sha": commit_sha,
            }
        )
        return SimpleNamespace(
            indexed_count=len(chunks),
            duration_ms=0,
            batches=[],
            embedded_count=len(chunks),
            cache_hit_count=0,
            pruned_count=0,
            total_current_count=len(chunks),
        )

    def delete_job(self, job_id: str) -> None:
        self.deleted_job_ids.append(job_id)


class _RecordingChunkRepository:
    def __init__(self) -> None:
        self.documents: list[Any] = []

    async def insert_one(self, document: Any) -> str:
        self.documents.append(document)
        return "chunk-id"

    async def replace_for_job(
        self,
        *,
        job_id: object,
        documents: list[Any],
        batch_size: int,
    ) -> tuple[int, int]:
        _ = job_id, batch_size
        self.documents = list(documents)
        return len(documents), 1 if documents else 0


class _RecordingManifestRepository:
    def __init__(self) -> None:
        self.documents: list[Any] = []

    async def upsert_for_job(self, document: Any) -> None:
        self.documents.append(document)


class _RecordingTraceRepository:
    def __init__(self) -> None:
        self.documents: list[Any] = []

    async def insert_one(self, document: Any) -> str:
        self.documents.append(document)
        return "trace-id"


class _RecordingCodeIndexingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def index(
        self,
        *,
        review_job: object,
        sandbox_path: Path,
        filtered_files: list[Path],
        issues: list[object],
    ) -> None:
        self.calls.append(
            {
                "review_job": review_job,
                "sandbox_path": sandbox_path,
                "filtered_files": filtered_files,
                "issues": issues,
            }
        )


class _RecordingRepoSummaryRepository:
    def __init__(self) -> None:
        self.documents: list[object] = []

    async def insert_one(self, document: object) -> str:
        self.documents.append(document)
        return "summary-id"


class _SuccessfulRepoSummaryService:
    def build_prompt(self, _sandbox_path: Path) -> str:
        return "prompt"

    async def generate_summary(self, _prompt: str) -> RepoSummary:
        return RepoSummary(
            purpose="A tiny test API.",
            project_type="REST API backend",
            tech_stack=["Python", "FastAPI"],
            architecture_overview="A compact layered API.",
        )


class _FailingRepoSummaryService:
    def build_prompt(self, _sandbox_path: Path) -> str:
        return "prompt"

    async def generate_summary(self, _prompt: str) -> RepoSummary:
        raise RuntimeError("summary LLM unavailable")
