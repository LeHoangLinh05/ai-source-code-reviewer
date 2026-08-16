"""GitHub provider implementation for publishing pull requests using a bot account."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.services.git_provider.base import (
    ForkResult,
    GitProviderAuthenticationError,
    GitProviderConfigurationError,
    GitProviderPermissionError,
    GitProviderPublishError,
    PullRequestResult,
)

GITHUB_ACCEPT_HEADER = "application/vnd.github+json"
GITHUB_HTTP_TIMEOUT_SECONDS = 15.0
GITHUB_FORK_READY_ATTEMPTS = 5
GITHUB_FORK_READY_DELAY_SECONDS = 1.0
GITHUB_AUTH_USERNAME = "x-access-token"
GITHUB_REPOSITORY_PATH_PART_COUNT = 2
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_FORBIDDEN = 403
HTTP_STATUS_NOT_FOUND = 404
HTTP_STATUS_UNPROCESSABLE_ENTITY = 422
HTTP_STATUS_CREATED = 201
HTTP_STATUS_ACCEPTED = 202


class GitHubProvider:
    """GitHub REST integration authenticated via a centralized bot PAT."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client

    def get_bot_access_token(self) -> str:
        """Return the configured bot access token."""

        bot_username = self.settings.github_bot_username
        token = _secret_value(self.settings.github_bot_token)
        if not bot_username or not token:
            raise GitProviderConfigurationError(
                "GitHub bot username and token are required for publishing"
            )

        return token

    async def get_branch_head_sha(
        self,
        *,
        repository_full_name: str,
        branch: str,
        token: str,
    ) -> str:
        """Return the current branch HEAD SHA for stale-base detection."""

        encoded_branch = quote(branch, safe="")
        response = await self._request(
            "GET",
            f"/repos/{repository_full_name}/branches/{encoded_branch}",
            token=token,
        )
        payload = self._json_object(response)
        commit = payload.get("commit")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not sha:
            raise GitProviderPublishError("GitHub branch response did not include HEAD")

        return sha

    async def create_pull_request(
        self,
        *,
        repository_full_name: str,
        head: str,
        base: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequestResult:
        """Create a pull request for a pushed fix branch."""

        response = await self._request(
            "POST",
            f"/repos/{repository_full_name}/pulls",
            token=token,
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            },
        )
        payload = self._json_object(response)
        pr_url = _string_payload(payload, "html_url")
        if pr_url is None:
            raise GitProviderPublishError("GitHub PR response did not include a URL")

        return PullRequestResult(url=pr_url)

    async def create_or_get_fork(
        self,
        *,
        repository_full_name: str,
        fork_owner: str,
        token: str,
    ) -> ForkResult:
        """Create or reuse a fork repository for the contributor account."""

        response = await self._request(
            "POST",
            f"/repos/{repository_full_name}/forks",
            token=token,
            expected_statuses={HTTP_STATUS_ACCEPTED, HTTP_STATUS_CREATED},
            tolerate_existing=True,
        )
        if response.status_code == HTTP_STATUS_UNPROCESSABLE_ENTITY:
            return await self._get_existing_fork(
                repository_full_name=repository_full_name,
                fork_owner=fork_owner,
                token=token,
            )

        payload = self._json_object(response)
        fork_full_name = _string_payload(payload, "full_name")
        clone_url = _string_payload(payload, "clone_url")
        if fork_full_name is None:
            fork_full_name = _build_fork_full_name(
                repository_full_name=repository_full_name,
                fork_owner=fork_owner,
            )
        if clone_url is None:
            clone_url = build_github_https_url(fork_full_name)

        return await self._wait_for_fork_ready(
            fork_full_name=fork_full_name,
            clone_url=clone_url,
            token=token,
        )

    async def _get_existing_fork(
        self,
        *,
        repository_full_name: str,
        fork_owner: str,
        token: str,
    ) -> ForkResult:
        fork_full_name = _build_fork_full_name(
            repository_full_name=repository_full_name,
            fork_owner=fork_owner,
        )
        response = await self._request(
            "GET",
            f"/repos/{fork_full_name}",
            token=token,
            expected_statuses={200},
        )
        payload = self._json_object(response)
        return ForkResult(
            full_name=fork_full_name,
            clone_url=_string_payload(payload, "clone_url")
            or build_github_https_url(fork_full_name),
        )

    async def _wait_for_fork_ready(
        self,
        *,
        fork_full_name: str,
        clone_url: str,
        token: str,
    ) -> ForkResult:
        for _attempt in range(GITHUB_FORK_READY_ATTEMPTS):
            response = await self._request(
                "GET",
                f"/repos/{fork_full_name}",
                token=token,
                expected_statuses={200},
                tolerate_not_found=True,
            )
            if response.status_code == HTTP_STATUS_NOT_FOUND:
                await asyncio.sleep(GITHUB_FORK_READY_DELAY_SECONDS)
                continue

            return ForkResult(full_name=fork_full_name, clone_url=clone_url)

        raise GitProviderPublishError("GitHub fork was not ready for publishing")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        auth_scheme: str = "Bearer",
        expected_statuses: set[int] | None = None,
        tolerate_existing: bool = False,
        tolerate_not_found: bool = False,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        expected = expected_statuses or {200, HTTP_STATUS_CREATED}
        response = await self._send_request(
            method,
            path,
            token=token,
            auth_scheme=auth_scheme,
            json=json,
        )
        if response.status_code in expected:
            return response
        if (
            tolerate_existing
            and response.status_code == HTTP_STATUS_UNPROCESSABLE_ENTITY
        ):
            return response
        if tolerate_not_found and response.status_code == HTTP_STATUS_NOT_FOUND:
            return response

        detail = _github_error_message(response)
        if response.status_code == HTTP_STATUS_UNAUTHORIZED:
            raise GitProviderAuthenticationError(detail)
        if response.status_code == HTTP_STATUS_FORBIDDEN:
            raise GitProviderPermissionError(detail)

        raise GitProviderPublishError(detail)

    async def _send_request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        auth_scheme: str,
        json: dict[str, object] | None,
    ) -> httpx.Response:
        headers = {
            "Accept": GITHUB_ACCEPT_HEADER,
            "Authorization": f"{auth_scheme} {token}",
            "X-GitHub-Api-Version": self.settings.github_api_version,
        }
        if self.http_client is not None:
            return await self.http_client.request(
                method, path, headers=headers, json=json
            )

        async with httpx.AsyncClient(
            base_url=self.settings.github_api_base_url,
            timeout=GITHUB_HTTP_TIMEOUT_SECONDS,
        ) as client:
            return await client.request(method, path, headers=headers, json=json)

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise GitProviderPublishError(
                "GitHub returned an invalid JSON response"
            ) from error

        if not isinstance(payload, dict):
            raise GitProviderPublishError("GitHub returned an unexpected response")

        return payload


def parse_github_repository_full_name(repository_url: str) -> str:
    """Return owner/repo from a GitHub HTTPS repository URL."""

    parsed_url = urlparse(repository_url)
    if parsed_url.hostname != "github.com":
        raise GitProviderPublishError("Only github.com repositories are supported")

    path_parts = [part for part in parsed_url.path.strip("/").split("/") if part]
    if len(path_parts) < GITHUB_REPOSITORY_PATH_PART_COUNT:
        raise GitProviderPublishError("GitHub repository URL is missing owner or name")

    owner = path_parts[0]
    repository_name = path_parts[1].removesuffix(".git")
    return f"{owner}/{repository_name}"


def build_github_https_url(repository_full_name: str) -> str:
    """Return the canonical HTTPS clone URL for a GitHub repository."""

    return f"https://github.com/{repository_full_name}.git"


def build_authenticated_github_url(repository_url: str, token: str) -> str:
    """Return a Git HTTPS URL authenticated with a token."""

    parsed_url = urlparse(repository_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
        raise GitProviderPublishError("GitHub publish requires an HTTPS repository URL")

    netloc = f"{GITHUB_AUTH_USERNAME}:{quote(token, safe='')}@{parsed_url.hostname}"
    return parsed_url._replace(netloc=netloc).geturl()


def _github_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"GitHub request failed with status {response.status_code}"

    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    return f"GitHub request failed with status {response.status_code}"


def _secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None

    value = secret.get_secret_value().strip()
    return value or None


def _string_payload(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def _build_fork_full_name(
    *,
    repository_full_name: str,
    fork_owner: str,
) -> str:
    repository_name = repository_full_name.split("/", maxsplit=1)[1]
    return f"{fork_owner}/{repository_name}"
