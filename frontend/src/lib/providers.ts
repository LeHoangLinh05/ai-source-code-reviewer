import { api } from "@/lib/api";
import type {
  GitHubInstallUrlResponse,
  GitHubInstallationSyncRequest,
  ProviderConnection,
  RepositoryProviderStatus,
} from "@/types/provider";

export async function getGitHubInstallUrl() {
  const response = await api.get<GitHubInstallUrlResponse>(
    "/providers/github/install-url",
  );
  return response.data;
}

export function buildGitHubInstallUrl(installUrl: string, state: string) {
  const url = new URL(installUrl);
  url.searchParams.set("state", state);
  return url.toString();
}

export function getSafeProviderReturnPath(state: string | null) {
  if (state === null || state.trim() === "") {
    return "/reviews";
  }

  if (!state.startsWith("/") || state.startsWith("//")) {
    return "/reviews";
  }

  return state;
}

export async function getProviderConnections() {
  const response = await api.get<ProviderConnection[]>("/providers/connections");
  return response.data;
}

export async function getRepositoryProviderStatus(repositoryId: string) {
  const response = await api.get<RepositoryProviderStatus>(
    `/repositories/${repositoryId}/provider-status`,
  );
  return response.data;
}

export async function syncGitHubInstallation(
  payload: GitHubInstallationSyncRequest,
) {
  const response = await api.post<ProviderConnection>(
    "/providers/github/installations/sync",
    payload,
  );
  return response.data;
}
