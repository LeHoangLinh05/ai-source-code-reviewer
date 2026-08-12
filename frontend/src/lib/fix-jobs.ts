import { api } from "@/lib/api";
import type {
  CreateFixPayload,
  FixAuditLog,
  FixDiff,
  FixJob,
  PublishFixPayload,
} from "@/types/fix-job";

export async function createFixJob(jobId: string, payload: CreateFixPayload) {
  const response = await api.post<FixJob>(`/review-jobs/${jobId}/fixes`, payload);
  return response.data;
}

export async function listFixJobs(jobId: string) {
  const response = await api.get<FixJob[]>(`/review-jobs/${jobId}/fixes`);
  return response.data;
}

export async function getFixJob(fixId: string) {
  const response = await api.get<FixJob>(`/fixes/${fixId}`);
  return response.data;
}

export async function getFixDiff(fixId: string) {
  const response = await api.get<FixDiff>(`/fixes/${fixId}/diff`);
  return response.data;
}

export async function publishFixJob(fixId: string, payload: PublishFixPayload) {
  const response = await api.post<FixJob>(`/fixes/${fixId}/publish`, payload);
  return response.data;
}

export async function retryFixPublish(fixId: string) {
  const response = await api.post<FixJob>(`/fixes/${fixId}/retry-publish`);
  return response.data;
}

export async function cancelFixPublish(fixId: string) {
  const response = await api.post<FixJob>(`/fixes/${fixId}/cancel`);
  return response.data;
}

export async function listFixAuditLogs(fixId: string) {
  const response = await api.get<FixAuditLog[]>(`/fixes/${fixId}/audit`);
  return response.data;
}
