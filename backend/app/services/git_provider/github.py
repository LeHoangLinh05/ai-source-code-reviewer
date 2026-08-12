"""GitHub App provider implementation for publishing pull requests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from jose import jwt
from pydantic import SecretStr

from app.core.config import Settings
from app.services.git_provider.base import (
    ForkResult,
    GitProviderAuthenticationError,
    GitProviderConfigurationError,
    GitProviderPermissionError,
    GitProviderPublishError,
    InstallationAccessToken,
    ProviderInstallationDetails,
    PullRequestResult,
)

GITHUB_ACCEPT_HEADER = "application/vnd.github+json"
GITHUB_APP_JWT_ALGORITHM = "RS256"
GITHUB_APP_JWT_TTL_SECONDS = 9 * 60
GITHUB_APP_JWT_CLOCK_SKEW_SECONDS = 60
GITHUB_HTTP_TIMEOUT_SECONDS = 15.0
GITHUB_FORK_READY_ATTEMPTS = 5
GITHUB_FORK_READY_DELAY_SECONDS = 1.0
GITHUB_APP_AUTH_USERNAME = "x-access-token"
GITHUB_REPOSITORY_PATH_PART_COUNT = 2
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_FORBIDDEN = 403
HTTP_STATUS_NOT_FOUND = 404
HTTP_STATUS_UNPROCESSABLE_ENTITY = 422
HTTP_STATUS_CREATED = 201
HTTP_STATUS_ACCEPTED = 202


class GitHubProvider:
    """GitHub REST integration authenticated as a GitHub App installation."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client

    async def create_installation_access_token(
        self,
        installation_id: str,
    ) -> InstallationAccessToken:
        """Create a short-lived installation token from GitHub App credentials."""

        app_jwt = self._build_app_jwt()
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=app_jwt,
            auth_scheme="Bearer",
        )
        payload = self._json_object(response)
        token = _string_payload(payload, "token")
        if token is None:
            raise GitProviderAuthenticationError(
                "GitHub did not return an installation token"
            )

        expires_at = _parse_optional_datetime(_string_payload(payload, "expires_at"))
        return InstallationAccessToken(token=token, expires_at=expires_at)

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

    async def get_installation_details(
        self,
        installation_id: str,
    ) -> ProviderInstallationDetails:
        """Return GitHub App installation metadata for the setup callback."""

        app_jwt = self._build_app_jwt()
        response = await self._request(
            "GET",
            f"/app/installations/{installation_id}",
            token=app_jwt,
            auth_scheme="Bearer",
        )
        payload = self._json_object(response)
        account = payload.get("account")
        if not isinstance(account, dict):
            raise GitProviderPublishError(
                "GitHub installation response did not include account details"
            )

        account_login = _string_payload(account, "login")
        if account_login is None:
            raise GitProviderPublishError(
                "GitHub installation response did not include an account login"
            )

        account_type = _string_payload(account, "type")
        repository_selection = _string_payload(payload, "repository_selection")
        permissions = payload.get("permissions")
        if not isinstance(permissions, dict):
            permissions = None

        resolved_installation_id = installation_id
        installation_value = payload.get("id")
        if isinstance(installation_value, int) and not isinstance(
            installation_value, bool
        ):
            resolved_installation_id = str(installation_value)
        elif isinstance(installation_value, str) and installation_value.strip():
            resolved_installation_id = installation_value.strip()

        return ProviderInstallationDetails(
            installation_id=resolved_installation_id,
            account_login=account_login,
            account_type=account_type,
            repository_selection=repository_selection,
            permissions=permissions,
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

    def _build_app_jwt(self) -> str:
        app_id = self.settings.github_app_id
        private_key = _secret_value(self.settings.github_app_private_key)
        if app_id is None or private_key is None:
            raise GitProviderConfigurationError(
                "GitHub App ID and private key are required for publishing"
            )

        now = datetime.now(UTC)
        issued_at = now - timedelta(seconds=GITHUB_APP_JWT_CLOCK_SKEW_SECONDS)
        expires_at = now + timedelta(seconds=GITHUB_APP_JWT_TTL_SECONDS)
        return jwt.encode(
            {
                "iat": int(issued_at.timestamp()),
                "exp": int(expires_at.timestamp()),
                "iss": app_id,
            },
            private_key,
            algorithm=GITHUB_APP_JWT_ALGORITHM,
        )

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
    """Return a Git HTTPS URL authenticated with an installation token."""

    parsed_url = urlparse(repository_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
        raise GitProviderPublishError("GitHub publish requires an HTTPS repository URL")

    netloc = f"{GITHUB_APP_AUTH_USERNAME}:{quote(token, safe='')}@{parsed_url.hostname}"
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


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_fork_full_name(
    *,
    repository_full_name: str,
    fork_owner: str,
) -> str:
    repository_name = repository_full_name.split("/", maxsplit=1)[1]
    return f"{fork_owner}/{repository_name}"
