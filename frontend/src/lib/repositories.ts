import { api } from "@/lib/api";
import type {
  CreateRepositoryPayload,
  DeleteRepositoryResponse,
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
