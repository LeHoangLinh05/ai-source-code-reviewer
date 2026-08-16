"""Provider abstractions shared by Git publish integrations."""

from dataclasses import dataclass
from typing import Protocol


class GitProviderError(RuntimeError):
    """Base provider error surfaced to publish workflows."""


class GitProviderConfigurationError(GitProviderError):
    """Raised when required provider settings are missing."""


class GitProviderAuthenticationError(GitProviderError):
    """Raised when provider authentication fails."""


class GitProviderPermissionError(GitProviderError):
    """Raised when the provider denies a write operation."""


class GitProviderPublishError(GitProviderError):
    """Raised when a provider publish operation fails."""


@dataclass(slots=True)
class ForkResult:
    """Provider fork metadata used for fork pull requests."""

    full_name: str
    clone_url: str


@dataclass(slots=True)
class PullRequestResult:
    """Provider pull request metadata returned after creation."""

    url: str


class GitProvider(Protocol):
    """Minimum source-control provider contract for publishing fixes."""

    def get_bot_access_token(self) -> str:
        """Return the configured bot access token."""

    async def get_branch_head_sha(
        self,
        *,
        repository_full_name: str,
        branch: str,
        token: str,
    ) -> str:
        """Return the current HEAD SHA for a provider branch."""

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
        """Create a pull request and return its public URL."""

    async def create_or_get_fork(
        self,
        *,
        repository_full_name: str,
        fork_owner: str,
        token: str,
    ) -> ForkResult:
        """Create or reuse a fork repository for contributor publishing."""
