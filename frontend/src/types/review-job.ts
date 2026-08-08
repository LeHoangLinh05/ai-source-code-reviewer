export type ReviewJobStatus =
  | "PENDING"
  | "CLONING"
  | "ANALYZING_STRUCTURE"
  | "GENERATING_SUMMARY"
  | "RUNNING_STATIC_ANALYSIS"
  | "CHUNKING_CODE"
  | "AI_REVIEWING"
  | "GENERATING_REPORT"
  | "COMPLETED"
  | "FAILED";

export type JobProgressEventType =
  | "status_change"
  | "progress_update"
  | "log"
  | "completed"
  | "failed";

export type JobProgressEvent = {
  job_id: string;
  event: JobProgressEventType;
  status: ReviewJobStatus;
  progress: number;
  message: string;
  timestamp: string;
  data: Record<string, unknown>;
};

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

export type AIToolCallTrace = {
  sequence: number;
  tool_name: string;
  called_at: string;
  duration_ms: number;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  status: "ok" | "error" | string;
  event_type: "tool" | "llm" | "embedding" | "pipeline" | string;
  provider: string | null;
  model: string | null;
  phase: string | null;
  token_usage: Record<string, number> | null;
  metadata: Record<string, unknown>;
};

export type AITraceCoverage = {
  total_reviewable_files: number;
  total_reviewable_lines: number;
  review_mode: string;
  chunked_files: number;
  total_chunks: number;
  target_files: number;
  target_chunks: number;
  ai_read_files: number;
  ai_read_chunks: number;
  ai_retrieved_chunks: number;
  ai_judged_chunks: number;
  ai_read_target_chunks: number;
  ai_read_file_percent: number;
  ai_read_chunk_percent: number;
  static_analyzer_runs: number;
  static_analyzer_issues: number;
  generated_ai_issues: number;
  generated_report_by_ai: boolean;
};

export type AITraceStage = {
  key: string;
  label: string;
  status: "pending" | "running" | "completed" | "warning" | "failed" | "skipped" | string;
  detail: string;
  progress_percent: number;
  current: number;
  total: number;
};

export type AITrace = {
  job_id: string;
  has_ai_started: boolean;
  tool_call_count: number;
  latest_tool_name: string | null;
  latest_tool_status: string | null;
  issue_counts_by_source: Record<string, number>;
  ai_issue_count: number;
  static_issue_count: number;
  report_model: string | null;
  report_created_at: string | null;
  coverage: AITraceCoverage;
  stages: AITraceStage[];
  recent_tool_calls: AIToolCallTrace[];
  events: AIToolCallTrace[];
  token_totals: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    estimated_input_tokens: number;
  };
};

export type ReviewMode = "smart" | "full_audit";

export type ReviewJobOptions = {
  review_mode?: ReviewMode;
  rule_profile?: {
    id: "roadmap_bootcamp_v1";
    weeks_included?: number[] | null;
  } | null;
  [key: string]: unknown;
};

export type CreateReviewJobPayload = {
  repository_id: string;
  branch: string;
  commit_sha?: string | null;
  options?: ReviewJobOptions;
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

export type DeleteReviewJobResponse = {
  message: string;
};

export type UpdateReviewJobStatusPayload = {
  message?: string;
  progress: number;
  status: ReviewJobStatus;
};
