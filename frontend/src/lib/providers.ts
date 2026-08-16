import { api } from "@/lib/api";
import type { RepositoryProviderStatus } from "@/types/provider";

export async function getRepositoryProviderStatus(repositoryId: string) {
  const response = await api.get<RepositoryProviderStatus>(
    `/repositories/${repositoryId}/provider-status`,
  );
  return response.data;
}
