"""Tests for AI LLM provider configuration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.ai.llm_config as llm_config


@pytest.mark.asyncio
async def test_run_with_configured_llm_uses_openai_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = object()
    calls: list[object] = []

    monkeypatch.setattr(
        llm_config,
        "get_settings",
        lambda: SimpleNamespace(llm_provider="openai"),
    )
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: model)

    async def callback(llm: Any) -> str:
        calls.append(llm)
        return "ok"

    result = await llm_config.run_with_configured_llm(callback)

    assert result == "ok"
    assert calls == [model]


@pytest.mark.asyncio
async def test_run_with_configured_llm_uses_nvidia_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nvidia_model = object()
    openai_model = object()
    calls: list[object] = []

    monkeypatch.setattr(
        llm_config,
        "get_settings",
        lambda: SimpleNamespace(llm_provider="nvidia"),
    )
    monkeypatch.setattr(llm_config, "get_nvidia_llm", lambda: nvidia_model)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)

    async def callback(llm: Any) -> str:
        calls.append(llm)
        return "ok"

    result = await llm_config.run_with_configured_llm(callback)

    assert result == "ok"
    assert calls == [nvidia_model]


@pytest.mark.asyncio
async def test_run_with_configured_llm_falls_back_to_openai_after_nvidia_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nvidia_model = object()
    openai_model = object()
    calls: list[object] = []

    monkeypatch.setattr(
        llm_config,
        "get_settings",
        lambda: SimpleNamespace(llm_provider="nvidia"),
    )
    monkeypatch.setattr(llm_config, "get_nvidia_llm", lambda: nvidia_model)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)

    async def callback(llm: Any) -> str:
        calls.append(llm)
        if llm is nvidia_model:
            raise RuntimeError("NIM format error")
        return "ok"

    result = await llm_config.run_with_configured_llm(callback)

    assert result == "ok"
    assert calls == [nvidia_model, openai_model]
