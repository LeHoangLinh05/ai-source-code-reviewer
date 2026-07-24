"""Tests for AI LLM provider configuration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from app.ai.llm import config as llm_config


def test_get_openai_llm_uses_configured_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = object()
    captured_options: dict[str, object] = {}
    api_key = SecretStr("cline-test-key")
    settings = SimpleNamespace(
        openai_api_key=api_key,
        openai_base_url="https://api.cline.bot/api/v1",
        openai_model="cline-pass/qwen3.7-plus",
    )

    def build_model(**options: object) -> object:
        captured_options.update(options)
        return model

    monkeypatch.setattr(llm_config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_config, "OpenAICompatibleChatOpenAI", build_model)

    result = llm_config.get_openai_llm()

    assert result is model
    assert captured_options == {
        "model": "cline-pass/qwen3.7-plus",
        "api_key": api_key,
        "base_url": "https://api.cline.bot/api/v1",
        "temperature": 0,
        "max_retries": 0,
    }


def test_compatible_chat_model_unwraps_clinepass_response() -> None:
    model = llm_config.OpenAICompatibleChatOpenAI(
        model="cline-pass/qwen3.7-plus",
        api_key=SecretStr("cline-test-key"),
        base_url="https://api.cline.bot/api/v1",
    )
    result = model._create_chat_result(
        {
            "success": True,
            "data": {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "cline-pass/qwen3.7-plus",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        }
    )

    assert result.generations[0].message.content == "ok"
    assert result.llm_output == {
        "token_usage": {
            "prompt_tokens": 3,
            "completion_tokens": 1,
            "total_tokens": 4,
        },
        "model_provider": "openai",
        "model_name": "cline-pass/qwen3.7-plus",
        "system_fingerprint": "",
        "id": "chatcmpl-test",
    }


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
async def test_allow_fallback_keeps_using_configured_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_model = object()
    calls: list[object] = []

    monkeypatch.setattr(
        llm_config,
        "get_settings",
        lambda: SimpleNamespace(llm_provider="openai"),
    )
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)

    async def callback(llm: Any) -> str:
        calls.append(llm)
        return "ok"

    result = await llm_config.run_with_configured_llm(
        callback,
        allow_fallback=True,
    )

    assert result == "ok"
    assert calls == [openai_model]


@pytest.mark.asyncio
async def test_rate_limit_opens_provider_circuits_and_stops_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_model = object()
    calls: list[object] = []
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_job_call_budget=10,
        llm_rate_limit_failure_budget=1,
        openai_max_retries=0,
    )
    monkeypatch.setattr(llm_config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)

    async def callback(llm: Any) -> str:
        calls.append(llm)
        error = RuntimeError("429 quota exceeded")
        error.status_code = 429  # type: ignore[attr-defined]
        raise error

    async with llm_config.llm_session():
        with pytest.raises(RuntimeError, match="429"):
            await llm_config.run_with_configured_llm(
                callback,
                allow_fallback=True,
            )
        with pytest.raises(llm_config.LLMCircuitOpenError):
            await llm_config.run_with_configured_llm(callback)

    assert calls == [openai_model]


class _FakePipelineModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def ainvoke(
        self,
        _input: object,
        config: object = None,
        **_kwargs: object,
    ) -> object:
        _ = config
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_openai_tpm_limit_waits_and_retries_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_limit = RuntimeError(
        "Rate limit reached on tokens per min. Please try again in 822ms."
    )
    rate_limit.status_code = 429  # type: ignore[attr-defined]
    openai_model = _FakePipelineModel([rate_limit, "continued"])
    delays: list[float] = []
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_job_call_budget=96,
        openai_max_retries=2,
    )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(llm_config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)
    monkeypatch.setattr(llm_config.asyncio, "sleep", record_sleep)

    async with llm_config.llm_session() as state:
        model = llm_config.get_pipeline_llm()
        result = await model.ainvoke("same-agent-step")

    assert result == "continued"
    assert openai_model.calls == 2
    assert delays == [pytest.approx(1.072)]
    assert state is not None
    assert not state.openai_circuit_open


@pytest.mark.asyncio
async def test_openai_timeout_retries_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_model = _FakePipelineModel([RuntimeError("Request timed out."), "continued"])
    delays: list[float] = []
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_job_call_budget=96,
        openai_max_retries=2,
    )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(llm_config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)
    monkeypatch.setattr(llm_config.asyncio, "sleep", record_sleep)

    async with llm_config.llm_session():
        model = llm_config.get_pipeline_llm()
        result = await model.ainvoke("same-agent-step")

    assert result == "continued"
    assert openai_model.calls == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_openai_requests_are_paced_within_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_model = _FakePipelineModel(["first", "second"])
    delays: list[float] = []
    monotonic_values = iter([100.0, 100.0, 100.25, 101.5])
    settings = SimpleNamespace(
        llm_provider="openai",
        llm_job_call_budget=96,
        openai_min_request_interval_seconds=1.5,
    )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(llm_config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_config, "get_openai_llm", lambda: openai_model)
    monkeypatch.setattr(llm_config.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(llm_config, "_monotonic", lambda: next(monotonic_values))

    async with llm_config.llm_session():
        model = llm_config.get_pipeline_llm()
        assert await model.ainvoke("first-step") == "first"
        assert await model.ainvoke("second-step") == "second"

    assert delays == [pytest.approx(1.25)]


def test_resource_exhausted_503_is_treated_as_provider_limit() -> None:
    error = RuntimeError(
        "Error code: 503 - {'error': {'message': "
        "'ResourceExhausted: Worker local total request limit reached (48/48)', "
        "'type': 'Service Unavailable', 'code': 503}}"
    )
    error.status_code = 503  # type: ignore[attr-defined]

    assert llm_config._is_rate_limit_error(error)


def test_unrelated_503_is_not_treated_as_provider_limit() -> None:
    error = RuntimeError("Error code: 503 - upstream service unavailable")
    error.status_code = 503  # type: ignore[attr-defined]

    assert not llm_config._is_rate_limit_error(error)


def test_extract_token_usage_from_usage_metadata() -> None:
    result = SimpleNamespace(
        usage_metadata={
            "input_tokens": 21,
            "output_tokens": 8,
            "total_tokens": 29,
        }
    )

    assert llm_config._extract_token_usage(result) == {
        "input_tokens": 21,
        "output_tokens": 8,
        "total_tokens": 29,
    }


def test_extract_token_usage_from_response_metadata() -> None:
    result = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
            }
        }
    )

    assert llm_config._extract_token_usage(result) == {
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
    }


def test_llm_trace_summarizes_messages_without_empty_payloads() -> None:
    trace = llm_config._llm_input_trace(
        [
            ("system", "You are reviewing code."),
            ("user", "Find hardcoded secrets in auth.py"),
        ]
    )

    assert trace["kind"] == "messages"
    assert trace["message_count"] == 2
    messages = trace["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert messages[0]["content"]["size_chars"] > 0
    assert messages[1]["content"]["preview"] == "Find hardcoded secrets in auth.py"


def test_llm_trace_summarizes_output_content() -> None:
    trace = llm_config._llm_output_trace(SimpleNamespace(content="review result"))

    assert trace["kind"] == "SimpleNamespace"
    content = cast(dict[str, object], trace["content"])
    assert content["preview"] == "review result"
