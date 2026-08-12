import { describe, expect, it } from "vitest";

import {
  buildActiveReviews,
  buildNeedsAttention,
  buildPrimaryAction,
  buildRiskPosture,
  selectRecentCompletedJobs,
  type DashboardData,
} from "./dashboard";
import type { Repository } from "../types/repository";
import type { ReviewReport } from "../types/report";
import type { ReviewJob, ReviewJobStatus } from "../types/review-job";

const DEFAULT_CREATED_AT = "2026-07-24T08:00:00Z";

function makeRepository(overrides: Partial<Repository> = {}): Repository {
  return {
    id: "repository-1",
    user_id: "user-1",
    name: "repo-guard",
    url: "https://github.com/example/repo-guard",
    platform: "github",
    default_branch: "main",
    description: null,
    last_reviewed_at: DEFAULT_CREATED_AT,
    created_at: DEFAULT_CREATED_AT,
    ...overrides,
  };
}

function makeJob(
  id: string,
  status: ReviewJobStatus = "COMPLETED",
  overrides: Partial<ReviewJob> = {},
): ReviewJob {
  return {
    id,
    repository_id: "repository-1",
    repository_name: "repo-guard",
    user_id: "user-1",
    status,
    branch: "main",
    commit_sha: null,
    error_message: null,
    options: null,
    started_at: DEFAULT_CREATED_AT,
    completed_at: status === "COMPLETED" ? DEFAULT_CREATED_AT : null,
    created_at: DEFAULT_CREATED_AT,
    stream_url: `/review-jobs/${id}/stream`,
    ...overrides,
  };
}

function makeReport(
  jobId: string,
  overrides: Partial<ReviewReport> = {},
): ReviewReport {
  return {
    id: `report-${jobId}`,
    job_id: jobId,
    total_files_analyzed: 10,
    total_issues: 0,
    total_findings: 0,
    total_occurrences: 0,
    total_raw_issues: 0,
    critical_count: 0,
    high_count: 0,
    medium_count: 0,
    low_count: 0,
    info_count: 0,
    security_score: null,
    maintainability_score: null,
    performance_score: null,
    overall_score: null,
    tech_stack: null,
    top_risky_files: null,
    executive_summary: null,
    analysis_overview: null,
    ai_model_used: null,
    created_at: DEFAULT_CREATED_AT,
    ...overrides,
  };
}

function makeData(overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    repositories: [makeRepository()],
    jobs: [],
    reports: [],
    reportLoadFailureJobIds: [],
    reportRequestCount: 0,
    ...overrides,
  };
}

describe("dashboard state derivation", () => {
  it("does not report a clear posture when completed reports fail to load", () => {
    const completedJob = makeJob("completed-job");
    const posture = buildRiskPosture(
      makeData({
        jobs: [completedJob],
        reportLoadFailureJobIds: [completedJob.id],
        reportRequestCount: 1,
      }),
    );

    expect(posture.tone).toBe("unavailable");
    expect(posture.title).toBe("Security posture is unavailable");
  });

  it("warns about incomplete coverage while preserving critical severity", () => {
    const loadedJob = makeJob("loaded-job");
    const failedJob = makeJob("failed-report-job");
    const posture = buildRiskPosture(
      makeData({
        jobs: [loadedJob, failedJob],
        reports: [
          makeReport(loadedJob.id, {
            total_findings: 2,
            critical_count: 2,
          }),
        ],
        reportLoadFailureJobIds: [failedJob.id],
        reportRequestCount: 2,
      }),
    );

    expect(posture.tone).toBe("critical");
    expect(posture.message).toContain("Report coverage is incomplete");
  });

  it("links the findings action to the latest report that has findings", () => {
    const latestClearJob = makeJob("latest-clear", "COMPLETED", {
      completed_at: "2026-07-24T10:00:00Z",
    });
    const olderRiskyJob = makeJob("older-risky", "COMPLETED", {
      completed_at: "2026-07-24T09:00:00Z",
    });
    const action = buildPrimaryAction(
      makeData({
        jobs: [latestClearJob, olderRiskyJob],
        reports: [
          makeReport(latestClearJob.id),
          makeReport(olderRiskyJob.id, {
            total_findings: 1,
            high_count: 1,
          }),
        ],
        reportRequestCount: 2,
      }),
    );

    expect(action).toEqual({
      label: "Review findings",
      href: "/reviews/older-risky/issues",
    });
  });

  it("keeps critical findings ahead of failed jobs when the list is limited", () => {
    const criticalJob = makeJob("critical-job");
    const failedJobs = Array.from({ length: 6 }, (_, index) =>
      makeJob(`failed-job-${index}`, "FAILED"),
    );
    const items = buildNeedsAttention(
      makeData({
        jobs: [criticalJob, ...failedJobs],
        reports: [
          makeReport(criticalJob.id, {
            total_findings: 1,
            critical_count: 1,
          }),
        ],
        reportRequestCount: 1,
      }),
      1,
    );

    expect(items).toHaveLength(1);
    expect(items[0]?.tone).toBe("critical");
  });

  it("distinguishes queued reviews from running reviews", () => {
    const queuedJob = makeJob("queued-job", "PENDING", {
      started_at: null,
      completed_at: null,
    });
    const data = makeData({ jobs: [queuedJob] });

    expect(buildActiveReviews(data)[0]?.isQueued).toBe(true);
    expect(buildRiskPosture(data).title).toBe("Your first review is queued");
  });

  it("selects recent reports by completion time before applying the limit", () => {
    const recentlyCompletedJob = makeJob("recently-completed", "COMPLETED", {
      created_at: "2026-07-20T08:00:00Z",
      completed_at: "2026-07-24T10:00:00Z",
    });
    const recentlyCreatedJob = makeJob("recently-created", "COMPLETED", {
      created_at: "2026-07-24T09:00:00Z",
      completed_at: "2026-07-24T09:30:00Z",
    });

    expect(
      selectRecentCompletedJobs(
        [recentlyCreatedJob, recentlyCompletedJob],
        1,
      )[0]?.id,
    ).toBe(recentlyCompletedJob.id);
  });
});
