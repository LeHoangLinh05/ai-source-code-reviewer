export type FixJobStatus =
  | "PENDING"
  | "PREPARING"
  | "GENERATING_PATCH"
  | "VALIDATING"
  | "WAITING_APPROVAL"
  | "APPROVED"
  | "FAILED";

export type FixValidationStatus = "NOT_RUN" | "PASSED" | "FAILED";

export type FixPublishStatus =
  | "NOT_REQUESTED"
  | "PUBLISHING"
  | "PUBLISHED"
  | "FAILED"
  | "NEEDS_FORK"
  | "STALE_BASE";

export type PublishStrategy = "fork";

export type FixValidationCheckStatus = "passed" | "failed" | "skipped";

export type FixValidationCheck = {
  name: string;
  command: string;
  status: FixValidationCheckStatus;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number;
};

export type FixValidationResult = {
  status: FixValidationStatus;
  summary: string;
  checks: FixValidationCheck[];
};

export type FixJobProgressEventType =
  | "status_change"
  | "progress_update"
  | "log"
  | "completed"
  | "failed";

export type FixJobProgressEvent = {
  fix_id: string;
  review_job_id: string;
  event: FixJobProgressEventType;
  status: FixJobStatus;
  progress: number;
  message: string;
  timestamp: string;
  data: Record<string, unknown>;
};

export type CreateFixPayload = {
  issue_ids: string[];
  target_branch?: string | null;
};

export type PublishFixPayload = {
  strategy: PublishStrategy;
  allow_failed_validation: boolean;
};

export type FixJob = {
  id: string;
  review_job_id: string;
  user_id: string;
  status: FixJobStatus;
  validation_status: FixValidationStatus;
  issue_ids: string[];
  target_branch: string;
  base_commit_sha: string;
  fix_branch: string;
  error_message: string | null;
  failure_reason: string | null;
  changed_files: string[] | null;
  validation_output:
    | FixValidationResult
    | Array<Record<string, unknown>>
    | null;
  validation_summary: FixValidationResult | null;
  publish_status: FixPublishStatus;
  publish_error: string | null;
  published_branch: string | null;
  published_commit_sha: string | null;
  provider: "github" | "gitlab" | "other" | null;
  publish_started_at: string | null;
  publish_completed_at: string | null;
  fork_repository_full_name: string | null;
  fork_branch: string | null;
  upstream_repository_full_name: string | null;
  pr_url: string | null;
  stream_url: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
};

export type FixDiff = {
  fix_id: string;
  diff: string;
  changed_files: string[];
};

export type FixAuditAction =
  | "FIX_GENERATED"
  | "DIFF_VIEWED"
  | "PUBLISH_APPROVED"
  | "BRANCH_PUSHED"
  | "PR_CREATED"
  | "PUBLISH_FAILED"
  | "PUBLISH_RETRIED"
  | "PUBLISH_CANCELED"
  | "NEEDS_FORK"
  | "STALE_BASE";

export type FixAuditLog = {
  id: string;
  fix_job_id: string;
  user_id: string | null;
  action: FixAuditAction;
  message: string | null;
  event_metadata: Record<string, unknown> | null;
  created_at: string;
};
