"""LangChain LLM configuration for AI review."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


def get_primary_llm() -> ChatGoogleGenerativeAI:
    """Return the primary Gemini chat model."""

    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=_secret_value(settings.gemini_api_key),
        temperature=0,
        max_retries=3,
    )


def get_fallback_llm() -> ChatOpenAI:
    """Return the fallback OpenAI chat model."""

    settings = get_settings()
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key=settings.openai_api_key,
        temperature=0,
        max_retries=3,
    )


async def run_with_llm_fallback(
    primary_call: Callable[[ChatGoogleGenerativeAI], Awaitable[ResultT]],
    fallback_call: Callable[[ChatOpenAI], Awaitable[ResultT]],
) -> ResultT:
    """Run the primary model and switch to fallback after a primary failure."""

    try:
        return await primary_call(get_primary_llm())
    except Exception as error:
        logger.warning(
            "Primary Gemini LLM failed after retries; switching to OpenAI fallback: %s",
            error,
            exc_info=True,
        )
        return await fallback_call(get_fallback_llm())


def _secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None

    value = secret.get_secret_value()
    return value or None
