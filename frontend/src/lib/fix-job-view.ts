import type {
  FixDiff,
  FixJob,
  FixJobStatus,
  FixPublishStatus,
} from "@/types/fix-job";

export type DiffFileSection = {
  filePath: string;
  diff: string;
};

export const ACTIVE_FIX_STATUSES: ReadonlySet<FixJobStatus> = new Set([
  "PENDING",
  "PREPARING",
  "GENERATING_PATCH",
  "VALIDATING",
]);

export const ACTIVE_FIX_PUBLISH_STATUSES: ReadonlySet<FixPublishStatus> = new Set([
  "PUBLISHING",
]);

export function isFixLive(fix: FixJob) {
  return (
    ACTIVE_FIX_STATUSES.has(fix.status) ||
    ACTIVE_FIX_PUBLISH_STATUSES.has(fix.publish_status)
  );
}

export function canPublishFix(fix: FixJob) {
  return (
    fix.status === "WAITING_APPROVAL" &&
    Boolean(fix.validation_summary) &&
    (fix.changed_files?.length ?? 0) > 0 &&
    !["PUBLISHING", "PUBLISHED", "STALE_BASE"].includes(fix.publish_status)
  );
}

export const canApproveFix = canPublishFix;

export function canPublishViaFork(fix: FixJob) {
  return canPublishFix(fix);
}

export function canRetryPublish(fix: FixJob) {
  return canPublishFix(fix) && fix.publish_status === "FAILED";
}

export function canCancelPublish(fix: FixJob) {
  return fix.publish_status === "PUBLISHING";
}

export function canShowFixDiff(fix: FixJob) {
  return ["WAITING_APPROVAL", "APPROVED", "FAILED"].includes(fix.status);
}

export function getFixFailureReason(fix: FixJob) {
  return fix.publish_error ?? fix.failure_reason ?? fix.error_message;
}

export function getFixProgressPercent(status: FixJobStatus): number {
  const progressByStatus: Record<FixJobStatus, number> = {
    PENDING: 0,
    PREPARING: 10,
    GENERATING_PATCH: 45,
    VALIDATING: 80,
    WAITING_APPROVAL: 100,
    APPROVED: 100,
    FAILED: 100,
  };
  return progressByStatus[status];
}

export function getFixProgressMessage(fixOrStatus: FixJob | FixJobStatus): string {
  if (typeof fixOrStatus !== "string") {
    const publishMessage = getFixPublishMessage(fixOrStatus);
    if (publishMessage) {
      return publishMessage;
    }

    return getFixProgressMessage(fixOrStatus.status);
  }

  const status = fixOrStatus;
  if (status === "WAITING_APPROVAL") {
    return "Patch is ready for review.";
  }
  if (status === "APPROVED") {
    return "Patch approved.";
  }
  if (status === "FAILED") {
    return "Fix job failed.";
  }

  return `Fix status: ${formatFixStatus(status)}`;
}

export function getFixPublishMessage(fix: FixJob): string | null {
  if (fix.publish_status === "PUBLISHING") {
    return "Publishing pull request.";
  }
  if (fix.publish_status === "PUBLISHED") {
    return "Pull request published.";
  }
  if (fix.publish_status === "NEEDS_FORK") {
    return "Publishing through your fork.";
  }
  if (fix.publish_status === "STALE_BASE") {
    return "Base branch changed; regenerate the fix before publishing.";
  }
  if (fix.publish_status === "FAILED") {
    return fix.publish_error ?? "Publish failed.";
  }

  return null;
}

export function splitUnifiedDiffByFile(diff: FixDiff): DiffFileSection[] {
  const lines = diff.diff.replaceAll("\r\n", "\n").split("\n");
  const sections: DiffFileSection[] = [];
  let currentLines: string[] = [];
  let currentFilePath: string | null = null;

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      if (currentLines.length > 0) {
        sections.push({
          filePath: currentFilePath ?? "patch.diff",
          diff: currentLines.join("\n"),
        });
      }

      currentLines = [line];
      currentFilePath = parseDiffFilePath(line);
      continue;
    }

    currentLines.push(line);
  }

  if (currentLines.length > 0) {
    sections.push({
      filePath: currentFilePath ?? diff.changed_files[0] ?? "patch.diff",
      diff: currentLines.join("\n"),
    });
  }

  if (sections.length > 0) {
    return sections;
  }

  return diff.changed_files.map((filePath) => ({
    filePath,
    diff: diff.diff,
  }));
}

export function formatFixStatus(status: FixJobStatus) {
  return status.replaceAll("_", " ").toLowerCase();
}

export function formatFixPublishStatus(status: FixPublishStatus) {
  return status.replaceAll("_", " ").toLowerCase();
}

function parseDiffFilePath(headerLine: string) {
  const parts = headerLine.split(" ");
  const targetPath = parts.find((part) => part.startsWith("b/"));
  if (targetPath) {
    return targetPath.slice(2);
  }

  const sourcePath = parts.find((part) => part.startsWith("a/"));
  return sourcePath ? sourcePath.slice(2) : "patch.diff";
}
