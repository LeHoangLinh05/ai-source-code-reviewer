import { describe, expect, it } from "vitest";

import reducer, { applyJobProgress, setCurrentJob } from "@/store/slices/jobSlice";
import type { JobProgressEvent, ReviewJob } from "@/types/review-job";

const job: ReviewJob = {
  id: "job-1",
  repository_id: "repository-1",
  repository_name: "Repo",
  user_id: "user-1",
  status: "AI_REVIEWING",
  branch: "main",
  commit_sha: null,
  error_message: null,
  options: null,
  started_at: "2026-07-28T10:00:00Z",
  completed_at: null,
  created_at: "2026-07-28T09:59:00Z",
  stream_url: "/api/review-jobs/job-1/stream",
};

function progressEvent(
  overrides: Partial<JobProgressEvent> = {},
): JobProgressEvent {
  return {
    job_id: "job-1",
    event: "progress_update",
    status: "AI_REVIEWING",
    progress: 91,
    message: "AI review batch 5 of 10 completed",
    timestamp: "2026-07-28T10:10:00Z",
    data: { completed_batches: 5, total_batches: 10 },
    ...overrides,
  };
}

describe("job progress state", () => {
  it("applies SSE status immediately to the current job", () => {
    const initialState = reducer(undefined, setCurrentJob(job));
    const state = reducer(
      initialState,
      applyJobProgress(
        progressEvent({
          event: "completed",
          status: "COMPLETED",
          progress: 100,
          timestamp: "2026-07-28T10:20:00Z",
        }),
      ),
    );

    expect(state.currentJob?.status).toBe("COMPLETED");
    expect(state.currentJob?.completed_at).toBe("2026-07-28T10:20:00Z");
  });
});
