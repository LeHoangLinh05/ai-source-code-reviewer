import type { RepositoryPlatform } from "@/types/repository";

export type GitHubInstallUrlResponse = {
  install_url: string;
};

export type GitHubInstallationSyncRequest = {
  installation_id: string;
};

export type ProviderConnection = {
  id: string;
  user_id: string;
  provider: RepositoryPlatform;
  installation_id: string;
  account_login: string;
  account_type: string | null;
  repository_selection: string | null;
  permissions: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type RepositoryProviderStatus = {
  repository_id: string;
  provider: RepositoryPlatform | null;
  is_connected: boolean;
  can_publish: boolean;
  installation_id: string | null;
  account_login: string | null;
  message: string;
};
