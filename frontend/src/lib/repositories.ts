import axios from "axios";

import { api } from "@/lib/api";
import type {
  CreateRepositoryPayload,
  DeleteRepositoryResponse,
  RepoSummary,
  Repository,
} from "@/types/repository";

export async function getRepositories() {
  const response = await api.get<Repository[]>("/repositories");
  return response.data;
}

export async function getRepository(repositoryId: string) {
  const response = await api.get<Repository>(`/repositories/${repositoryId}`);
  return response.data;
}

export async function getRepositorySummary(repositoryId: string) {
  try {
    const response = await api.get<RepoSummary>(
      `/repositories/${repositoryId}/summary`,
    );
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null;
    }

    throw error;
  }
}

export async function createRepository(payload: CreateRepositoryPayload) {
  const response = await api.post<Repository>("/repositories", payload);
  return response.data;
}

export async function deleteRepository(repositoryId: string) {
  const response = await api.delete<DeleteRepositoryResponse>(
    `/repositories/${repositoryId}`,
  );
  return response.data;
}
