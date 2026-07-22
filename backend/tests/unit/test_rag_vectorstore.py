"""Tests for mandatory RAG vector store dependencies."""

import pytest

import app.ai.rag.vectorstore as vectorstore_module
from app.ai.rag.vectorstore import validate_rag_dependencies


def test_validate_rag_dependencies_raises_when_required_package_missing(
    monkeypatch,
) -> None:
    def fake_find_spec(package_name: str) -> object | None:
        if package_name == "chromadb":
            return None
        return object()

    monkeypatch.setattr(vectorstore_module, "find_spec", fake_find_spec)

    with pytest.raises(RuntimeError, match="chromadb"):
        validate_rag_dependencies()


def test_validate_rag_dependencies_passes_when_required_packages_exist(
    monkeypatch,
) -> None:
    monkeypatch.setattr(vectorstore_module, "find_spec", lambda _name: object())

    validate_rag_dependencies()
