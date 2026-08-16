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

export async function disconnectProviderConnection(connectionId: string) {
  await api.delete(`/providers/connections/${connectionId}`);
}

export function buildGitHubInstallationManageUrl(
  connection: Pick<
    ProviderConnection,
    "account_login" | "account_type" | "installation_id"
  >,
) {
  const installationId = encodeURIComponent(connection.installation_id);
  if (connection.account_type?.toLowerCase() === "organization") {
    const accountLogin = encodeURIComponent(connection.account_login);
    return `https://github.com/organizations/${accountLogin}/settings/installations/${installationId}`;
  }

  return `https://github.com/settings/installations/${installationId}`;
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
