import type { RepositoryPlatform } from "@/types/repository";

export type RepositoryProviderStatus = {
  repository_id: string;
  provider: RepositoryPlatform | null;
  is_connected: boolean;
  can_publish: boolean;
  installation_id?: string | null;
  account_login: string | null;
  message: string;
};
