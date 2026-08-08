import { describe, expect, it } from "vitest";

import { canPublishFix, splitUnifiedDiffByFile } from "@/lib/fix-job-view";
import type { FixDiff, FixJob } from "@/types/fix-job";

function buildFixJob(overrides: Partial<FixJob> = {}): FixJob {
  return {
    id: "fix-1",
    review_job_id: "job-1",
    user_id: "user-1",
    status: "WAITING_APPROVAL",
    validation_status: "PASSED",
    issue_ids: ["issue-1"],
    target_branch: "main",
    base_commit_sha: "a".repeat(40),
    fix_branch: "repoguard/fix/fix-1",
    error_message: null,
    failure_reason: null,
    changed_files: ["src/app.py"],
    validation_output: null,
    validation_summary: {
      status: "PASSED",
      summary: "Validation passed: 1 passed, 0 failed, 0 skipped.",
      checks: [
        {
          name: "ruff check",
          command: "ruff check src/app.py",
          status: "passed",
          exit_code: 0,
          stdout: "",
          stderr: "",
          duration_ms: 12,
        },
      ],
    },
    publish_status: "NOT_REQUESTED",
    publish_error: null,
    published_branch: null,
    published_commit_sha: null,
    provider: null,
    publish_started_at: null,
    publish_completed_at: null,
    fork_repository_full_name: null,
    fork_branch: null,
    upstream_repository_full_name: null,
    pr_url: null,
    stream_url: "/api/fixes/fix-1/stream",
    started_at: null,
    completed_at: null,
    created_at: "2026-07-30T10:00:00Z",
    ...overrides,
  };
}

describe("fix job view helpers", () => {
  it("allows publish only when the generated patch is ready", () => {
    expect(canPublishFix(buildFixJob())).toBe(true);
    expect(canPublishFix(buildFixJob({ status: "VALIDATING" }))).toBe(false);
    expect(canPublishFix(buildFixJob({ changed_files: [] }))).toBe(false);
    expect(canPublishFix(buildFixJob({ validation_summary: null }))).toBe(false);
    expect(canPublishFix(buildFixJob({ publish_status: "PUBLISHING" }))).toBe(
      false,
    );
  });

  it("splits unified diff preview by changed file", () => {
    const diff: FixDiff = {
      fix_id: "fix-1",
      changed_files: ["src/app.py", "src/utils.py"],
      diff: [
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1 +1 @@",
        "-old",
        "+new",
        "diff --git a/src/utils.py b/src/utils.py",
        "--- a/src/utils.py",
        "+++ b/src/utils.py",
        "@@ -1 +1 @@",
        "-bad",
        "+good",
      ].join("\n"),
    };

    expect(splitUnifiedDiffByFile(diff)).toEqual([
      {
        filePath: "src/app.py",
        diff: [
          "diff --git a/src/app.py b/src/app.py",
          "--- a/src/app.py",
          "+++ b/src/app.py",
          "@@ -1 +1 @@",
          "-old",
          "+new",
        ].join("\n"),
      },
      {
        filePath: "src/utils.py",
        diff: [
          "diff --git a/src/utils.py b/src/utils.py",
          "--- a/src/utils.py",
          "+++ b/src/utils.py",
          "@@ -1 +1 @@",
          "-bad",
          "+good",
        ].join("\n"),
      },
    ]);
  });
});
