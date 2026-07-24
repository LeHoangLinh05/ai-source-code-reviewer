"""OpenAI-compatible HTTP client for remote code embeddings."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import Any

logger = logging.getLogger(__name__)

MAX_RETRYABLE_HTTP_STATUS_CODE = 599
MAX_EMBEDDING_RETRY_DELAY_SECONDS = 60.0


class _OpenAICompatibleCodeEmbedder:
    """Remote embedding adapter for OpenAI-compatible embedding APIs."""

    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        api_key: str,
        base_url: str,
        output_dimension: int | None,
        max_retries: int,
        retry_base_delay_seconds: float,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.output_dimension = output_dimension
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.last_token_usage: dict[str, int] | None = None
        self._client: Any | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="passage")

    def embed_query(self, text: str) -> list[float]:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, input_type="query")

    def close(self) -> None:
        """Close the reusable HTTP client if it has been opened."""

        if self._client is None:
            return

        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._client = None

    def _embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if not texts:
            return []

        try:
            import httpx
        except ImportError as error:
            raise RuntimeError(
                "httpx is required for remote code semantic search embeddings"
            ) from error

        payload: dict[str, object] = {
            "model": self.model_name,
            "input": texts,
            "encoding_format": "float",
        }
        _ = input_type
        if self.provider == "mistral":
            if self.output_dimension is not None:
                payload["output_dimension"] = self.output_dimension
            payload["output_dtype"] = "float"

        if self._client is None:
            self._client = httpx.Client(timeout=60.0)

        response = self._post_with_retries(self._client, payload)

        response_payload = response.json()
        self.last_token_usage = _token_usage_from_response(response_payload)
        return _embeddings_from_response(response_payload)

    def _post_with_retries(self, client: Any, payload: dict[str, object]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self.headers,
                json=payload,
            )
            try:
                response.raise_for_status()
                return response
            except Exception as error:
                last_error = error
                if not _should_retry_embedding_response(
                    response, attempt, self.max_retries
                ):
                    detail = _embedding_error_detail(response)
                    raise RuntimeError(
                        f"{self.provider} code embedding request failed: "
                        f"HTTP {response.status_code}{detail}"
                    ) from error

                delay_seconds = _embedding_retry_delay_seconds(
                    response=response,
                    attempt=attempt,
                    base_delay_seconds=self.retry_base_delay_seconds,
                )
                logger.warning(
                    "%s code embedding request failed with HTTP %s; retrying in %.1fs",
                    self.provider,
                    response.status_code,
                    delay_seconds,
                )
                time.sleep(delay_seconds)

        raise RuntimeError(
            f"{self.provider} code embedding request failed after retries"
        ) from last_error


def _code_embedding_api_key(*, provider: str, settings: Any) -> str:
    if provider == "mistral":
        secret = settings.mistral_api_key
    elif provider == "openrouter":
        secret = settings.openrouter_api_key
    else:
        secret = settings.openai_api_key
    api_key = secret.get_secret_value() if secret is not None else ""
    if not api_key:
        raise RuntimeError(f"{provider} API key is required for code embeddings")

    return api_key


def _code_embedding_base_url(*, provider: str, settings: Any) -> str:
    if settings.code_embedding_base_url:
        return str(settings.code_embedding_base_url)

    if provider == "mistral":
        return str(settings.mistral_base_url)

    if provider == "openrouter":
        return str(settings.openrouter_base_url)

    return "https://api.openai.com/v1"


def _should_retry_embedding_response(
    response: Any,
    attempt: int,
    max_retries: int,
) -> bool:
    if attempt >= max_retries:
        return False

    status_code = int(getattr(response, "status_code", 0) or 0)
    return (
        status_code == HTTPStatus.TOO_MANY_REQUESTS
        or HTTPStatus.INTERNAL_SERVER_ERROR
        <= status_code
        <= MAX_RETRYABLE_HTTP_STATUS_CODE
    )


def _embedding_retry_delay_seconds(
    *,
    response: Any,
    attempt: int,
    base_delay_seconds: float,
) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return retry_after

    return min(
        base_delay_seconds * (2**attempt),
        MAX_EMBEDDING_RETRY_DELAY_SECONDS,
    )


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    value = headers.get("retry-after")
    if value is None:
        value = headers.get("Retry-After")
    if value is None:
        return None

    try:
        delay = float(value)
    except ValueError:
        return None

    return max(0.0, delay)


def _embedding_error_detail(response: Any) -> str:
    text = getattr(response, "text", "")
    if not isinstance(text, str) or not text.strip():
        return ""

    compact_text = " ".join(text.split())
    return f": {compact_text[:500]}"


def _embeddings_from_response(response_payload: object) -> list[list[float]]:
    if not isinstance(response_payload, dict):
        raise RuntimeError("Code embedding API returned an invalid response")

    data = response_payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Code embedding API response has no data array")

    indexed_embeddings: list[tuple[int, object]] = []
    for default_index, item in enumerate(data):
        if not isinstance(item, dict):
            raise RuntimeError("Code embedding API returned an invalid item")

        index = item.get("index", default_index)
        if not isinstance(index, int):
            raise RuntimeError("Code embedding API returned an invalid index")

        indexed_embeddings.append((index, item.get("embedding")))

    return [
        embedding
        for _index, embedding in sorted(
            indexed_embeddings,
            key=lambda indexed_embedding: indexed_embedding[0],
        )
        if isinstance(embedding, list)
    ]


def _token_usage_from_response(response_payload: object) -> dict[str, int] | None:
    if not isinstance(response_payload, dict):
        return None
    usage = response_payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _usage_int(usage, ("input_tokens", "prompt_tokens"))
    total_tokens = _usage_int(usage, ("total_tokens",))
    if total_tokens == 0:
        total_tokens = input_tokens
    result = {
        "input_tokens": input_tokens,
        "total_tokens": total_tokens,
    }
    return result if any(result.values()) else None


def _usage_int(value: dict[str, object], keys: tuple[str, ...]) -> int:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and item >= 0:
            return item
    return 0
