import { api } from "@/lib/api";
import type {
  CancelReviewJobResponse,
  CreateReviewJobPayload,
  CreateReviewJobResponse,
  ReviewJob,
  ReviewJobFilters,
  UpdateReviewJobStatusPayload,
} from "@/types/review-job";

export async function createReviewJob(payload: CreateReviewJobPayload) {
  const response = await api.post<CreateReviewJobResponse>("/review-jobs", payload);
  return response.data;
}

export async function getReviewJobs(filters: ReviewJobFilters = {}) {
  const response = await api.get<ReviewJob[]>("/review-jobs", {
    params: filters,
  });
  return response.data;
}

export async function getReviewJob(jobId: string) {
  const response = await api.get<ReviewJob>(`/review-jobs/${jobId}`);
  return response.data;
}

export async function cancelReviewJob(jobId: string) {
  const response = await api.delete<CancelReviewJobResponse>(
    `/review-jobs/${jobId}`,
  );
  return response.data;
}

export async function updateReviewJobStatus(
  jobId: string,
  payload: UpdateReviewJobStatusPayload,
) {
  const response = await api.patch<ReviewJob>(
    `/review-jobs/${jobId}/status`,
    payload,
  );
  return response.data;
}
