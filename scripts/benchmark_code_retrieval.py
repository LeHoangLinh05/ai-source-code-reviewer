"""Offline code retrieval benchmark runner for Phase 0."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC, datetime
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

BENCHMARK_DIR = (
    Path(__file__).resolve().parents[1] / "backend" / "benchmarks" / "code_retrieval"
)
DEFAULT_CORPORA_PATH = BENCHMARK_DIR / "corpora.json"
DEFAULT_GOLD_PATH = BENCHMARK_DIR / "gold_queries.jsonl"
DEFAULT_OUTPUT_DIR = Path(".tmp") / "benchmarks" / "code-retrieval"
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@dataclass(slots=True, frozen=True)
class QueryResult:
    query_id: str
    repo: str
    query_type: str
    category: str
    hit_at_5: bool
    hit_at_10: bool
    reciprocal_rank: float
    duration_ms: int
    matched_paths: list[str]


@dataclass(slots=True, frozen=True)
class CorpusStageResult:
    repo: str
    commit_sha: str
    selected_files: int
    chunk_count: int
    scan_ms: int
    chunk_ms: int
    index_ms: int
    query_ms: int
    embedded_count: int
    cache_hit_count: int
    skipped_sensitive_count: int


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--corpora", type=Path, default=DEFAULT_CORPORA_PATH)
    materialize.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "corpora"
    )

    run = subparsers.add_parser("run")
    run.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    run.add_argument("--corpus-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "corpora")
    run.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--run-name", default=None)

    run_production = subparsers.add_parser("run-production")
    run_production.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    run_production.add_argument(
        "--corpora", type=Path, default=DEFAULT_CORPORA_PATH
    )
    run_production.add_argument(
        "--corpus-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "corpora"
    )
    run_production.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    run_production.add_argument("--run-name", default=None)
    run_production.add_argument("--branch", default="benchmark")

    compare = subparsers.add_parser("compare")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_DIR / "comparison.md"
    )

    export_job_parser = subparsers.add_parser("export-job")
    export_job_parser.add_argument("--job-id", required=True)
    export_job_parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_DIR / "job-export.json"
    )

    args = parser.parse_args()
    if args.command == "materialize":
        materialize_corpora(args.corpora, args.output_dir)
    elif args.command == "run":
        run_benchmark(args.gold, args.corpus_dir, args.output_dir, args.run_name)
    elif args.command == "run-production":
        run_production_benchmark(
            gold_path=args.gold,
            corpora_path=args.corpora,
            corpus_dir=args.corpus_dir,
            output_dir=args.output_dir,
            run_name=args.run_name,
            branch=args.branch,
        )
    elif args.command == "compare":
        compare_runs(args.baseline, args.candidate, args.output)
    elif args.command == "export-job":
        export_job(args.job_id, args.output)


def materialize_corpora(corpora_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for corpus in _load_corpora(corpora_path):
        target = output_dir / str(corpus["id"])
        if not target.exists():
            _run(["git", "clone", str(corpus["url"]), str(target)], cwd=None)
        _run(
            ["git", "fetch", "--depth", "1", "origin", str(corpus["commit_sha"])],
            cwd=target,
        )
        _run(["git", "checkout", "--detach", str(corpus["commit_sha"])], cwd=target)


def run_benchmark(
    gold_path: Path,
    corpus_dir: Path,
    output_dir: Path,
    run_name: str | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.perf_counter()
    queries = _load_jsonl(gold_path)
    results = [
        _evaluate_query(query, corpus_dir / str(query["repo"])) for query in queries
    ]
    summary = _summarize(results)
    run_id = run_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_ms": int((time.perf_counter() - started_at) * 1000),
        "query_count": len(results),
        "peak_rss_bytes": _current_rss_bytes(),
        "summary": summary,
        "results": [asdict(result) for result in results],
    }
    json_path = output_dir / f"{run_id}.json"
    markdown_path = output_dir / f"{run_id}.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    markdown_path.write_text(_markdown_report(payload), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")


def run_production_benchmark(
    *,
    gold_path: Path,
    corpora_path: Path,
    corpus_dir: Path,
    output_dir: Path,
    run_name: str | None,
    branch: str,
) -> None:
    """Run the benchmark through the production semantic code retriever."""

    from app.ai.rag.code_embedding import (
        CodeEmbeddingStore,
        code_embedding_model_version,
        prepare_code_embedding_chunks,
    )
    from app.ai.rag.code_retriever import CodeSemanticRetriever
    from app.core.config import get_settings

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-production")
    chroma_path = output_dir / "chroma" / run_id
    settings = get_settings()
    started_at = time.perf_counter()
    queries = _load_jsonl(gold_path)
    corpora = _load_corpora(corpora_path)
    queries_by_repo: dict[str, list[dict[str, Any]]] = {}
    for query in queries:
        queries_by_repo.setdefault(str(query["repo"]), []).append(query)

    store = CodeEmbeddingStore(persist_path=chroma_path)
    retriever = CodeSemanticRetriever(vectorstore=store)
    results: list[QueryResult] = []
    stage_results: list[CorpusStageResult] = []
    try:
        for corpus in corpora:
            repo_id = str(corpus["id"])
            repo_path = corpus_dir / repo_id
            if not repo_path.exists():
                raise RuntimeError(
                    f"Corpus {repo_id} is missing at {repo_path}; run materialize first"
                )

            commit_sha = _git_output(["git", "rev-parse", "HEAD"], cwd=repo_path)
            scan_started_at = time.perf_counter()
            file_manifest = _production_file_manifest(repo_path, settings)
            scan_ms = _duration_ms(scan_started_at)

            chunk_started_at = time.perf_counter()
            chunks = _production_chunk_documents(
                repo_path=repo_path,
                file_paths=file_manifest.files,
            )
            prepared_chunks = prepare_code_embedding_chunks(
                chunks,
                repository_id=repo_id,
                branch=branch,
                commit_sha=commit_sha,
                provider=settings.code_embedding_provider,
                model_name=settings.code_embedding_model,
                model_version=code_embedding_model_version(
                    settings.code_embedding_provider
                ),
                dimension=settings.code_embedding_dimension,
            )
            chunk_ms = _duration_ms(chunk_started_at)

            index_started_at = time.perf_counter()
            index_summary = store.index_chunks(prepared_chunks)
            index_ms = _duration_ms(index_started_at)

            index_generation_key = _index_generation_key(prepared_chunks)
            repo_queries = queries_by_repo.get(repo_id, [])
            query_started_at = time.perf_counter()
            repo_results = _evaluate_production_queries(
                queries=repo_queries,
                retriever=retriever,
                repo_branch_key=_repo_branch_key(prepared_chunks),
                index_generation_key=index_generation_key,
                amortized_started_at=query_started_at,
            )
            query_ms = _duration_ms(query_started_at)
            results.extend(repo_results)
            stage_results.append(
                CorpusStageResult(
                    repo=repo_id,
                    commit_sha=commit_sha,
                    selected_files=len(file_manifest.files),
                    chunk_count=len(prepared_chunks),
                    scan_ms=scan_ms,
                    chunk_ms=chunk_ms,
                    index_ms=index_ms,
                    query_ms=query_ms,
                    embedded_count=index_summary.embedded_count,
                    cache_hit_count=index_summary.cache_hit_count,
                    skipped_sensitive_count=index_summary.skipped_sensitive_count,
                )
            )
    finally:
        store.close()

    results_by_id = {result.query_id: result for result in results}
    ordered_results = [
        results_by_id[str(query["id"])]
        for query in queries
        if str(query["id"]) in results_by_id
    ]
    summary = _summarize(ordered_results)
    payload = {
        "run_id": run_id,
        "mode": "production-semantic",
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_ms": int((time.perf_counter() - started_at) * 1000),
        "query_count": len(ordered_results),
        "peak_rss_bytes": _current_rss_bytes(),
        "embedding": {
            "provider": settings.code_embedding_provider,
            "model": settings.code_embedding_model,
            "dimension": settings.code_embedding_dimension,
        },
        "chroma_path": str(chroma_path),
        "summary": summary,
        "stages": [asdict(stage) for stage in stage_results],
        "results": [asdict(result) for result in ordered_results],
    }
    json_path = output_dir / f"{run_id}.json"
    markdown_path = output_dir / f"{run_id}.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    markdown_path.write_text(_markdown_report(payload), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")


def compare_runs(baseline_path: Path, candidate_path: Path, output_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    lines = [
        "# Code Retrieval Benchmark Comparison",
        "",
        "| metric | baseline | candidate | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for metric in ("recall_at_5", "recall_at_10", "mrr_at_10", "p50_ms", "p95_ms"):
        left = float(baseline["summary"].get(metric, 0))
        right = float(candidate["summary"].get(metric, 0))
        lines.append(f"| {metric} | {left:.4f} | {right:.4f} | {right - left:.4f} |")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")


def export_job(job_id: str, output_path: Path) -> None:
    payload = {
        "job_id": job_id,
        "status": "not_connected",
        "message": "Run benchmark export inside an environment with Mongo access.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")


def _evaluate_query(query: dict[str, Any], repo_path: Path) -> QueryResult:
    started_at = time.perf_counter()
    expected_paths = [
        str(item["file_path"])
        for item in query.get("expected_evidence", [])
        if isinstance(item, dict) and item.get("file_path")
    ]
    matched_paths = _lexical_rank_paths(str(query["query"]), repo_path)[:10]
    first_rank = next(
        (
            index
            for index, path in enumerate(matched_paths, start=1)
            if path in expected_paths
        ),
        None,
    )
    return QueryResult(
        query_id=str(query["id"]),
        repo=str(query["repo"]),
        query_type=str(query["type"]),
        category=str(query["category"]),
        hit_at_5=first_rank is not None and first_rank <= 5,
        hit_at_10=first_rank is not None and first_rank <= 10,
        reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        matched_paths=matched_paths,
    )


def _evaluate_production_queries(
    *,
    queries: list[dict[str, Any]],
    retriever: Any,
    repo_branch_key: str | None,
    index_generation_key: str | None,
    amortized_started_at: float,
) -> list[QueryResult]:
    if not queries:
        return []

    requests = [
        _production_request(
            query=query,
            repo_branch_key=repo_branch_key,
            index_generation_key=index_generation_key,
        )
        for query in queries
    ]
    grouped_chunks = retriever.search_many(requests)
    duration_ms = max(0, int((time.perf_counter() - amortized_started_at) * 1000))
    amortized_duration_ms = max(0, round(duration_ms / max(len(queries), 1)))
    return [
        _production_query_result(
            query=query,
            chunks=chunks,
            duration_ms=amortized_duration_ms,
        )
        for query, chunks in zip(queries, grouped_chunks, strict=True)
    ]


def _production_request(
    *,
    query: dict[str, Any],
    repo_branch_key: str | None,
    index_generation_key: str | None,
) -> Any:
    from app.ai.rag.code_retriever import CodeSemanticSearchRequest

    return CodeSemanticSearchRequest(
        query=str(query["query"]),
        job_id=str(query["repo"]),
        repo_branch_key=repo_branch_key,
        index_generation_key=index_generation_key,
        top_k=10,
    )


def _production_query_result(
    *,
    query: dict[str, Any],
    chunks: list[Any],
    duration_ms: int,
) -> QueryResult:
    expected_paths = [
        str(item["file_path"])
        for item in query.get("expected_evidence", [])
        if isinstance(item, dict) and item.get("file_path")
    ]
    matched_paths = [
        str(chunk.metadata.get("file_path") or "")
        for chunk in chunks
        if chunk.metadata.get("file_path")
    ][:10]
    first_rank = next(
        (
            index
            for index, path in enumerate(matched_paths, start=1)
            if path in expected_paths
        ),
        None,
    )
    return QueryResult(
        query_id=str(query["id"]),
        repo=str(query["repo"]),
        query_type=str(query["type"]),
        category=str(query["category"]),
        hit_at_5=first_rank is not None and first_rank <= 5,
        hit_at_10=first_rank is not None and first_rank <= 10,
        reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
        duration_ms=duration_ms,
        matched_paths=matched_paths,
    )


def _lexical_rank_paths(query: str, repo_path: Path) -> list[str]:
    if not repo_path.exists():
        return []
    terms = {term for term in _tokens(query) if len(term) >= 3}
    scores: list[tuple[float, str]] = []
    for file_path in repo_path.rglob("*"):
        if not file_path.is_file() or ".git" in file_path.parts:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative_path = file_path.relative_to(repo_path).as_posix()
        searchable = f"{relative_path}\n{content[:12000]}".lower()
        matched = sum(1 for term in terms if term in searchable)
        if matched:
            scores.append((matched / max(len(terms), 1), relative_path))
    return [
        path for _score, path in sorted(scores, key=lambda item: item[0], reverse=True)
    ]


def _summarize(results: list[QueryResult]) -> dict[str, float]:
    durations = sorted(result.duration_ms for result in results)
    count = max(len(results), 1)
    return {
        "recall_at_5": sum(result.hit_at_5 for result in results) / count,
        "recall_at_10": sum(result.hit_at_10 for result in results) / count,
        "mrr_at_10": sum(result.reciprocal_rank for result in results) / count,
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
    }


def _markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Code Retrieval Benchmark",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- mode: `{payload.get('mode', 'lexical-baseline')}`",
        f"- query_count: `{payload['query_count']}`",
        f"- peak RSS: `{payload['peak_rss_bytes']} bytes`",
        f"- Recall@5: `{summary['recall_at_5']:.3f}`",
        f"- Recall@10: `{summary['recall_at_10']:.3f}`",
        f"- MRR@10: `{summary['mrr_at_10']:.3f}`",
        f"- p50 latency: `{summary['p50_ms']:.0f} ms`",
        f"- p95 latency: `{summary['p95_ms']:.0f} ms`",
        "",
    ]
    embedding = payload.get("embedding")
    if isinstance(embedding, dict):
        lines.extend(
            [
                "## Embedding",
                "",
                f"- provider: `{embedding.get('provider')}`",
                f"- model: `{embedding.get('model')}`",
                f"- dimension: `{embedding.get('dimension')}`",
                "",
            ]
        )
    stages = payload.get("stages")
    if isinstance(stages, list) and stages:
        lines.extend(
            [
                "## Stages",
                "",
                (
                    "| repo | files | chunks | scan ms | chunk ms | index ms | "
                    "query ms | embedded | cache hits | skipped sensitive |"
                ),
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            lines.append(
                "| {repo} | {selected_files} | {chunk_count} | {scan_ms} | "
                "{chunk_ms} | {index_ms} | {query_ms} | {embedded_count} | "
                "{cache_hit_count} | {skipped_sensitive_count} |".format(**stage)
            )
        lines.append("")
    return "\n".join(lines)


def _production_file_manifest(repo_path: Path, settings: Any) -> Any:
    from app.analyzers.file_filter import build_file_manifest

    return build_file_manifest(
        repo_path,
        max_source_file_size_bytes=settings.max_source_file_size_bytes,
    )


def _production_chunk_documents(
    *,
    repo_path: Path,
    file_paths: list[Path],
) -> list[Any]:
    from app.analyzers.code_chunker import chunk_python_file
    from app.services.review_pipeline_service import (
        build_plain_file_chunk_metadata_documents,
    )

    from uuid import uuid5, NAMESPACE_URL

    job_id = uuid5(NAMESPACE_URL, repo_path.resolve().as_posix())
    chunks: list[Any] = []
    python_files = {file_path for file_path in file_paths if file_path.suffix == ".py"}
    for file_path in python_files:
        for chunk in chunk_python_file(
            file_path,
            project_root=repo_path,
            static_issues=[],
        ):
            chunks.append(_chunk_document(job_id=job_id, chunk=chunk))

    for file_path in file_paths:
        if file_path in python_files:
            continue
        chunks.extend(
            build_plain_file_chunk_metadata_documents(
                job_id=job_id,
                sandbox_path=repo_path,
                file_path=file_path,
                issues=[],
            )
        )
    return chunks


def _chunk_document(*, job_id: Any, chunk: Any) -> Any:
    from app.schemas.mongodb import ChunkMetadataDocument

    metadata = chunk.metadata
    return ChunkMetadataDocument(
        job_id=job_id,
        file_path=metadata.file_path,
        language=metadata.language,
        chunk_type=metadata.chunk_type,
        chunk_index=metadata.chunk_index,
        total_chunks=metadata.total_chunks,
        function_name=metadata.function_name,
        class_name=metadata.class_name,
        line_start=metadata.line_start,
        line_end=metadata.line_end,
        imports=metadata.imports,
        module=metadata.module,
        risk_area=metadata.risk_area,
        has_static_issues=metadata.has_static_issues,
        token_count=metadata.token_count,
        chunk_text=chunk.content,
    )


def _repo_branch_key(chunks: list[Any]) -> str | None:
    for chunk in chunks:
        if chunk.repo_branch_key:
            return str(chunk.repo_branch_key)
    return None


def _index_generation_key(chunks: list[Any]) -> str | None:
    for chunk in chunks:
        if chunk.index_generation_key:
            return str(chunk.index_generation_key)
    return None


def _load_corpora(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in payload["corpora"] if isinstance(item, dict)]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _tokens(value: str) -> list[str]:
    return [
        token.lower() for token in value.replace("_", " ").replace("-", " ").split()
    ]


def _percentile(values: list[int], percentile: int) -> float:
    if not values:
        return 0.0
    index = round((len(values) - 1) * percentile / 100)
    return float(values[index])


def _current_rss_bytes() -> int:
    try:
        psutil = importlib.import_module("psutil")
    except ImportError:
        return 0

    return int(psutil.Process().memory_info().rss)


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _git_output(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _run(command: list[str], *, cwd: Path | None) -> None:
    subprocess.run(command, cwd=cwd, check=True, timeout=300)


if __name__ == "__main__":
    main()
