"""LangChain LLM provider configuration for AI review."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, TypeVar

from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI
from openai import BaseModel

from app.ai.llm.tracing import (
    _duration_ms,
    _extract_token_usage,
    _llm_input_trace,
    _llm_output_trace,
    _model_name,
    _write_llm_trace_event,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


class OpenAICompatibleChatOpenAI(ChatOpenAI):
    """Accept small response wrappers used by some OpenAI-compatible APIs."""

    def _create_chat_result(
        self,
        response: dict[str, Any] | BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        return super()._create_chat_result(
            _unwrap_openai_compatible_response(response),
            generation_info,
        )


class LLMCircuitOpenError(RuntimeError):
    """Raised when a job-level LLM safety budget has been exhausted."""


@dataclass(slots=True)
class LLMSessionState:
    """Bounded provider state shared by calls in one review job."""

    call_count: int = 0
    rate_limit_failures: int = 0
    openai_circuit_open: bool = False
    last_openai_request_at: float | None = None


_session_state: ContextVar[LLMSessionState | None] = ContextVar(
    "llm_session_state",
    default=None,
)


def get_openai_llm() -> ChatOpenAI:
    """Return the OpenAI chat model used by the review pipeline."""

    settings = get_settings()
    return OpenAICompatibleChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0,
        max_retries=0,
    )


def _unwrap_openai_compatible_response(
    response: dict[str, Any] | BaseModel,
) -> dict[str, Any] | BaseModel:
    response_dict = response if isinstance(response, dict) else response.model_dump()
    data = response_dict.get("data")
    choices = response_dict.get("choices")
    if isinstance(data, dict) and choices is None:
        return data

    return response


class ManagedOpenAILLM(Runnable[Any, Any]):
    """Apply bounded transient-rate retries to direct OpenAI pipelines."""

    def __init__(self, model: ChatOpenAI, state: LLMSessionState) -> None:
        self.model = model
        self.state = state

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        return _invoke_model(
            self.model,
            "openai",
            self.state,
            input,
            config,
            kwargs,
        )

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        return await _ainvoke_model(
            self.model,
            "openai",
            self.state,
            input,
            config,
            kwargs,
        )


def get_pipeline_llm() -> ManagedOpenAILLM:
    """Return the configured model for a complete side-effecting review pipeline."""

    state = _session_state.get()
    if state is None:
        raise RuntimeError(
            "LLM session must be configured before creating pipeline LLM"
        )
    return ManagedOpenAILLM(get_openai_llm(), state)


async def run_with_configured_llm(
    call: Callable[[ChatOpenAI], Awaitable[ResultT]],
    *,
    allow_fallback: bool = False,
) -> ResultT:
    """Run one bounded LLM call without replaying side-effecting pipelines."""

    _ = allow_fallback
    state, token = _get_or_create_session_state()
    try:
        return await _run_provider_call(call, get_openai_llm(), "openai", state)
    finally:
        if token is not None:
            _session_state.reset(token)


@asynccontextmanager
async def llm_session():
    """Bind one circuit-breaker state to a complete review job."""

    existing = _session_state.get()
    if existing is not None:
        yield existing
        return

    token = _session_state.set(LLMSessionState())
    try:
        yield _session_state.get()
    finally:
        _session_state.reset(token)


async def _run_provider_call(
    call: Callable[[ChatOpenAI], Awaitable[ResultT]],
    llm: ChatOpenAI,
    provider: str,
    state: LLMSessionState,
) -> ResultT:
    attempt = 0
    while True:
        _before_model_request(provider, state)
        await _pace_async_request(provider, state)
        started_at = datetime.now(UTC)
        perf_started_at = time.perf_counter()
        sequence = state.call_count
        try:
            result = await call(llm)
            await _write_llm_trace_event(
                provider=provider,
                model=_model_name(llm),
                sequence=sequence,
                called_at=started_at,
                duration_ms=_duration_ms(perf_started_at),
                status="ok",
                input_payload={
                    "call_style": "callback",
                    "summary": "LLM input is managed by the caller callback",
                },
                output=_llm_output_trace(result),
                token_usage=_extract_token_usage(result),
            )
            return result
        except Exception as error:
            await _write_llm_trace_event(
                provider=provider,
                model=_model_name(llm),
                sequence=sequence,
                called_at=started_at,
                duration_ms=_duration_ms(perf_started_at),
                status="error",
                input_payload={
                    "call_style": "callback",
                    "summary": "LLM input is managed by the caller callback",
                },
                output={"error": str(error), "error_type": type(error).__name__},
            )
            if _should_retry_transient_error(provider, error, attempt):
                delay = _rate_limit_retry_delay(error, attempt)
                logger.warning(
                    "Transient OpenAI failure; retrying small call in %.3fs: %s",
                    delay,
                    error,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            _record_model_failure(provider, state, error)
            raise


def _invoke_model(
    model: ChatOpenAI,
    provider: str,
    state: LLMSessionState,
    input: Any,
    config: RunnableConfig | None,
    kwargs: dict[str, Any],
) -> Any:
    attempt = 0
    while True:
        _before_model_request(provider, state)
        _pace_sync_request(provider, state)
        try:
            return model.invoke(input, config=config, **kwargs)
        except Exception as error:
            if _should_retry_transient_error(provider, error, attempt):
                delay = _rate_limit_retry_delay(error, attempt)
                logger.warning(
                    "Transient OpenAI failure; retrying request in %.3fs: %s",
                    delay,
                    error,
                )
                time.sleep(delay)
                attempt += 1
                continue
            _record_model_failure(provider, state, error)
            raise


async def _ainvoke_model(
    model: ChatOpenAI,
    provider: str,
    state: LLMSessionState,
    input: Any,
    config: RunnableConfig | None,
    kwargs: dict[str, Any],
) -> Any:
    attempt = 0
    while True:
        _before_model_request(provider, state)
        await _pace_async_request(provider, state)
        started_at = datetime.now(UTC)
        perf_started_at = time.perf_counter()
        sequence = state.call_count
        try:
            result = await model.ainvoke(input, config=config, **kwargs)
            await _write_llm_trace_event(
                provider=provider,
                model=_model_name(model),
                sequence=sequence,
                called_at=started_at,
                duration_ms=_duration_ms(perf_started_at),
                status="ok",
                input_payload=_llm_input_trace(input),
                output=_llm_output_trace(result),
                token_usage=_extract_token_usage(result),
            )
            return result
        except Exception as error:
            await _write_llm_trace_event(
                provider=provider,
                model=_model_name(model),
                sequence=sequence,
                called_at=started_at,
                duration_ms=_duration_ms(perf_started_at),
                status="error",
                input_payload=_llm_input_trace(input),
                output={"error": str(error), "error_type": type(error).__name__},
            )
            if _should_retry_transient_error(provider, error, attempt):
                delay = _rate_limit_retry_delay(error, attempt)
                logger.warning(
                    "Transient OpenAI failure; retrying request in %.3fs: %s",
                    delay,
                    error,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            _record_model_failure(provider, state, error)
            raise


def _should_retry_transient_error(
    provider: str,
    error: Exception,
    attempt: int,
) -> bool:
    if provider != "openai" or not _is_transient_provider_error(error):
        return False
    message = str(error).lower()
    permanent_markers = (
        "insufficient_quota",
        "billing_hard_limit",
        "exceeded your current quota",
        "quota exceeded",
    )
    if any(marker in message for marker in permanent_markers):
        return False
    settings = get_settings()
    return attempt < min(getattr(settings, "openai_max_retries", 2), 2)


def _rate_limit_retry_delay(error: Exception, attempt: int) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    retry_after_ms = _header_float(headers, "retry-after-ms")
    if retry_after_ms is not None:
        return min(10.0, max(0.1, retry_after_ms / 1000 + 0.25))
    retry_after = _header_float(headers, "retry-after")
    if retry_after is not None:
        return min(10.0, max(0.1, retry_after + 0.25))

    match = re.search(
        r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)",
        str(error),
        re.IGNORECASE,
    )
    if match is not None:
        value = float(match.group(1))
        seconds = value / 1000 if match.group(2).lower() == "ms" else value
        return min(10.0, max(0.1, seconds + 0.25))
    return min(10.0, float(2**attempt))


def _header_float(headers: object, name: str) -> float | None:
    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return None
    value = get_header(name)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _openai_request_interval() -> float:
    settings = get_settings()
    return float(getattr(settings, "openai_min_request_interval_seconds", 0.0))


def _remaining_openai_delay(state: LLMSessionState, now: float) -> float:
    if state.last_openai_request_at is None:
        return 0.0
    elapsed = now - state.last_openai_request_at
    return max(0.0, _openai_request_interval() - elapsed)


async def _pace_async_request(provider: str, state: LLMSessionState) -> None:
    if provider != "openai":
        return
    delay = _remaining_openai_delay(state, _monotonic())
    if delay > 0:
        logger.info("Pacing OpenAI request for %.3fs", delay)
        await asyncio.sleep(delay)
    state.last_openai_request_at = _monotonic()


def _pace_sync_request(provider: str, state: LLMSessionState) -> None:
    if provider != "openai":
        return
    delay = _remaining_openai_delay(state, _monotonic())
    if delay > 0:
        logger.info("Pacing OpenAI request for %.3fs", delay)
        time.sleep(delay)
    state.last_openai_request_at = _monotonic()


def _monotonic() -> float:
    return time.monotonic()


def _before_model_request(provider: str, state: LLMSessionState) -> None:
    settings = get_settings()
    call_budget = getattr(settings, "llm_job_call_budget", 96)
    if state.call_count >= call_budget:
        logger.error(
            "LLM job call budget exhausted; call_count=%d call_budget=%d",
            state.call_count,
            call_budget,
        )
        raise LLMCircuitOpenError("LLM job call budget exhausted")
    if provider == "openai" and state.openai_circuit_open:
        raise LLMCircuitOpenError("OpenAI circuit breaker is open")
    state.call_count += 1


def _record_model_failure(
    provider: str,
    state: LLMSessionState,
    error: Exception,
) -> None:
    if not _is_transient_provider_error(error):
        return
    state.rate_limit_failures += 1
    _open_provider_circuit(state, provider)
    logger.error(
        "LLM provider circuit opened after transient failure; provider=%s call=%d",
        provider,
        state.call_count,
    )


def _open_provider_circuit(state: LLMSessionState, provider: str) -> None:
    if provider == "openai":
        state.openai_circuit_open = True


def _get_or_create_session_state() -> tuple[
    LLMSessionState, Token[LLMSessionState | None] | None
]:
    state = _session_state.get()
    if state is not None:
        return state, None

    state = LLMSessionState()
    return state, _session_state.set(state)


def _is_rate_limit_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return True

    error_name = type(error).__name__.lower()
    message = str(error).lower()
    resource_exhausted = (
        "resourceexhausted" in error_name
        or "resourceexhausted" in message
        or "resource exhausted" in message
    )
    request_limit_reached = "request limit" in message and "reached" in message
    return (
        "ratelimit" in error_name
        or "rate limit" in message
        or "quota" in message
        or resource_exhausted
        or request_limit_reached
    )


def _is_timeout_error(error: Exception) -> bool:
    error_name = type(error).__name__.lower()
    message = str(error).lower()
    return (
        isinstance(error, TimeoutError)
        or "timeout" in error_name
        or "timed out" in message
    )


def _is_transient_provider_error(error: Exception) -> bool:
    return _is_rate_limit_error(error) or _is_timeout_error(error)
