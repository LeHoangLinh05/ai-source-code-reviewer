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
from typing import Any, TypeVar

from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


class LLMCircuitOpenError(RuntimeError):
    """Raised when a job-level LLM safety budget has been exhausted."""


@dataclass(slots=True)
class LLMSessionState:
    """Bounded provider state shared by calls in one review job."""

    call_count: int = 0
    rate_limit_failures: int = 0
    nvidia_circuit_open: bool = False
    openai_circuit_open: bool = False
    last_openai_request_at: float | None = None


_session_state: ContextVar[LLMSessionState | None] = ContextVar(
    "llm_session_state",
    default=None,
)


def get_openai_llm() -> ChatOpenAI:
    """Return the OpenAI chat model used by the review pipeline."""

    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=0,
    )


def get_nvidia_llm() -> ChatOpenAI:
    """Return the NVIDIA NIM chat model through its OpenAI-compatible API."""

    settings = get_settings()
    if not _has_secret_value(settings.nvidia_api_key):
        raise ValueError("NVIDIA_API_KEY is required when LLM_PROVIDER=nvidia")

    return ChatOpenAI(
        model=settings.nvidia_model,
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        temperature=0,
        max_retries=settings.nvidia_max_retries,
        timeout=settings.nvidia_timeout_seconds,
    )


class ProviderFailoverLLM(Runnable[Any, Any]):
    """Switch individual model requests without replaying agent tool calls."""

    def __init__(
        self,
        primary: ChatOpenAI,
        fallback_factory: Callable[[], ChatOpenAI],
        state: LLMSessionState,
    ) -> None:
        self.primary = primary
        self.fallback_factory = fallback_factory
        self.state = state
        self._fallback: ChatOpenAI | None = None

    @property
    def fallback(self) -> ChatOpenAI:
        if self._fallback is None:
            self._fallback = self.fallback_factory()
        return self._fallback

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        if self.state.nvidia_circuit_open:
            return _invoke_model(
                self.fallback,
                "openai",
                self.state,
                input,
                config,
                kwargs,
            )
        try:
            return _invoke_model(
                self.primary,
                "nvidia",
                self.state,
                input,
                config,
                kwargs,
            )
        except Exception as error:
            if not _is_transient_provider_error(error):
                raise
            _open_provider_circuit(self.state, "nvidia")
            logger.warning(
                "NVIDIA unavailable for this job; switching remaining requests "
                "to OpenAI: %s",
                error,
            )
            return _invoke_model(
                self.fallback,
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
        if self.state.nvidia_circuit_open:
            return await _ainvoke_model(
                self.fallback,
                "openai",
                self.state,
                input,
                config,
                kwargs,
            )
        try:
            return await _ainvoke_model(
                self.primary,
                "nvidia",
                self.state,
                input,
                config,
                kwargs,
            )
        except Exception as error:
            if not _is_transient_provider_error(error):
                raise
            _open_provider_circuit(self.state, "nvidia")
            logger.warning(
                "NVIDIA unavailable for this job; switching remaining requests "
                "to OpenAI: %s",
                error,
            )
            return await _ainvoke_model(
                self.fallback,
                "openai",
                self.state,
                input,
                config,
                kwargs,
            )


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


def get_pipeline_llm() -> ManagedOpenAILLM | ProviderFailoverLLM:
    """Return one model router for a complete side-effecting review pipeline."""

    settings = get_settings()
    state = _session_state.get()
    if state is None:
        raise RuntimeError(
            "LLM session must be configured before creating pipeline LLM"
        )
    if settings.llm_provider == "openai":
        return ManagedOpenAILLM(get_openai_llm(), state)
    return ProviderFailoverLLM(get_nvidia_llm(), get_openai_llm, state)


def get_remaining_llm_call_budget() -> int | None:
    """Return remaining job-level LLM calls for the active session, if any."""

    state = _session_state.get()
    if state is None:
        return None

    call_budget = getattr(get_settings(), "llm_job_call_budget", 96)
    return max(0, call_budget - state.call_count)


async def run_with_configured_llm(
    call: Callable[[ChatOpenAI], Awaitable[ResultT]],
    *,
    allow_fallback: bool = False,
) -> ResultT:
    """Run one bounded LLM call without replaying side-effecting pipelines."""

    settings = get_settings()
    state, token = _get_or_create_session_state()
    try:
        if settings.llm_provider == "openai":
            return await _run_provider_call(call, get_openai_llm(), "openai", state)

        if state.nvidia_circuit_open:
            if not allow_fallback:
                raise LLMCircuitOpenError("NVIDIA circuit breaker is open")
            return await _run_provider_call(call, get_openai_llm(), "openai", state)

        try:
            return await _run_provider_call(call, get_nvidia_llm(), "nvidia", state)
        except Exception as error:
            if not allow_fallback:
                raise
            logger.warning(
                "NVIDIA NIM small LLM call failed; using one OpenAI fallback: %s",
                error,
            )
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
        try:
            return await call(llm)
        except Exception as error:
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
        try:
            return await model.ainvoke(input, config=config, **kwargs)
        except Exception as error:
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
    if provider == "nvidia" and state.nvidia_circuit_open:
        raise LLMCircuitOpenError("NVIDIA circuit breaker is open")
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
    if provider == "nvidia":
        state.nvidia_circuit_open = True
    elif provider == "openai":
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
    if status_code == 429:
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


def _has_secret_value(secret: SecretStr | None) -> bool:
    if secret is None:
        return False

    return bool(secret.get_secret_value().strip())
