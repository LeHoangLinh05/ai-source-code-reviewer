export type ReviewJobStatus =
  | "PENDING"
  | "CLONING"
  | "ANALYZING_STRUCTURE"
  | "RUNNING_STATIC_ANALYSIS"
  | "CHUNKING_CODE"
  | "AI_REVIEWING"
  | "GENERATING_REPORT"
  | "COMPLETED"
  | "FAILED";

export type ReviewJob = {
  id: string;
  repository_id: string;
  repository_name: string | null;
  user_id: string;
  status: ReviewJobStatus;
  branch: string | null;
  commit_sha: string | null;
  error_message: string | null;
  options: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  stream_url: string;
};

export type CreateReviewJobPayload = {
  repository_id: string;
  branch: string;
  options: {
    run_static_analysis: boolean;
  };
};

export type CreateReviewJobResponse = {
  job_id: string;
  status: ReviewJobStatus;
  created_at: string;
  stream_url: string;
};

export type ReviewJobFilters = {
  repository_id?: string;
  status?: ReviewJobStatus;
};

export type CancelReviewJobResponse = {
  message: string;
};

export type UpdateReviewJobStatusPayload = {
  message?: string;
  progress: number;
  status: ReviewJobStatus;
};
