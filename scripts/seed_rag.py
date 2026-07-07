"""Seed the coding standards RAG knowledge base from local source documents."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "docs" / "rag_sources" / "sources.yaml"
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.rag.ingestion import RAGDocument, RAGIngestionPipeline  # noqa: E402
from app.ai.rag.retriever import HybridRetriever  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to the RAG sources YAML manifest.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing coding_standards collection before seeding.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip example retrieval queries after ingestion.",
    )
    args = parser.parse_args()

    pipeline = RAGIngestionPipeline()
    if args.reset:
        pipeline.reset()

    documents = load_documents_from_manifest(args.manifest)
    chunks = pipeline.ingest_documents(documents)
    print(f"Ingested {len(chunks)} chunks from {len(documents)} source documents.")

    if args.skip_smoke:
        return

    retriever = HybridRetriever(bm25_index=pipeline.bm25_index)
    for query in (
        "SQL injection prevention parameterized queries",
        "A07 authentication failures session credential stuffing",
        "Python naming conventions PEP 8",
        "FastAPI dependency injection APIRouter",
    ):
        results = retriever.search(query, language="python", top_k=3)
        print(f"\nQuery: {query}")
        for result in results:
            print(
                f"- {result.source} | final={result.final_score:.3f} "
                f"vector={result.vector_score:.3f} bm25={result.bm25_score:.3f}"
            )


def load_documents_from_manifest(manifest_path: Path) -> list[RAGDocument]:
    """Load all RAG source documents declared in a YAML manifest."""

    manifest = _load_manifest(manifest_path)
    documents: list[RAGDocument] = []
    for source_config in manifest.get("sources", []):
        documents.extend(_load_source_documents(source_config, manifest_path.parent))

    return documents


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"RAG sources manifest not found: {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise ValueError("RAG sources manifest must be a YAML mapping")

    return manifest


def _load_source_documents(
    source_config: dict[str, Any],
    manifest_dir: Path,
) -> list[RAGDocument]:
    required_fields = ("source", "path", "language", "doc_type", "category")
    missing_fields = [field for field in required_fields if field not in source_config]
    if missing_fields:
        raise ValueError(
            f"RAG source is missing required fields: {', '.join(missing_fields)}"
        )

    source_root = (manifest_dir / str(source_config["path"])).resolve()
    file_pattern = str(source_config.get("glob", "*.md"))
    if not source_root.is_dir():
        raise FileNotFoundError(f"RAG source directory not found: {source_root}")

    source_files = sorted(
        path for path in source_root.glob(file_pattern) if path.is_file()
    )
    if not source_files:
        raise ValueError(
            f"RAG source has no matching files: {source_root}/{file_pattern}"
        )

    documents: list[RAGDocument] = []
    for source_file in source_files:
        source_name = _document_source_name(str(source_config["source"]), source_file)
        documents.append(
            RAGDocument(
                source=source_name,
                content=source_file.read_text(encoding="utf-8", errors="ignore"),
                language=str(source_config["language"]),
                doc_type=str(source_config["doc_type"]),
                category=str(source_config["category"]),
                extra_metadata=_document_metadata(
                    source_config,
                    source_file=source_file,
                    source_root=source_root,
                ),
            )
        )

    return documents


def _document_source_name(source: str, source_file: Path) -> str:
    title = source_file.stem.replace("_", " ").replace("-", " ")
    return f"{source} - {title}"


def _document_metadata(
    source_config: dict[str, Any],
    *,
    source_file: Path,
    source_root: Path,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source_path": source_file.relative_to(PROJECT_ROOT).as_posix(),
        "source_file": source_file.name,
    }

    for key in ("version", "source_url", "repository", "license"):
        value = source_config.get(key)
        if value is not None:
            metadata[key] = value

    raw_base_url = source_config.get("raw_base_url")
    if raw_base_url:
        relative_source_path = source_file.relative_to(source_root).as_posix()
        metadata["raw_url"] = f"{raw_base_url.rstrip('/')}/{relative_source_path}"

    return metadata


if __name__ == "__main__":
    main()
