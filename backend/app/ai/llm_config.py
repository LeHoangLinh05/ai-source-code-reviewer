"""LangChain LLM provider configuration for AI review."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


def get_openai_llm() -> ChatOpenAI:
    """Return the OpenAI chat model used by the review pipeline."""

    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=settings.openai_max_retries,
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


async def run_with_configured_llm(
    call: Callable[[ChatOpenAI], Awaitable[ResultT]],
) -> ResultT:
    """Run one AI pipeline call with the configured provider.

    NVIDIA NIM is used as a cost-saving first pass when selected. OpenAI remains
    the reliability fallback for malformed responses, rate limits, and outages.
    """

    settings = get_settings()
    if settings.llm_provider == "openai":
        return await call(get_openai_llm())

    try:
        return await call(get_nvidia_llm())
    except Exception as error:
        logger.warning(
            "NVIDIA NIM LLM failed; switching to OpenAI fallback: %s",
            error,
            exc_info=True,
        )
        return await call(get_openai_llm())


def _has_secret_value(secret: SecretStr | None) -> bool:
    if secret is None:
        return False

    return bool(secret.get_secret_value().strip())
