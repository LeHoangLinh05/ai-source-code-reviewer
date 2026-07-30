import {
  CheckCircle2,
  CircleDashed,
  CirclePlay,
  LoaderCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { IssueSeverity } from "@/types/issue";
import type { ReviewJobStatus } from "@/types/review-job";

const SEVERITY_STYLES: Record<IssueSeverity, string> = {
  critical: "border-slate-400 text-slate-950 dark:text-slate-50",
  high: "border-slate-500 text-slate-900 dark:text-slate-100",
  medium: "border-slate-600 text-slate-800 dark:text-slate-200",
  low: "border-slate-700 text-slate-700 dark:text-slate-300",
  info: "border-border bg-muted text-muted-foreground",
};

const SEVERITY_DOT_STYLES: Record<IssueSeverity, string> = {
  critical: "bg-slate-950 dark:bg-slate-50",
  high: "bg-slate-800 dark:bg-slate-200",
  medium: "bg-slate-600 dark:bg-slate-400",
  low: "bg-slate-500 dark:bg-slate-500",
  info: "bg-muted-foreground",
};

const STATUS_ICONS: Record<ReviewJobStatus, LucideIcon> = {
  AI_REVIEWING: LoaderCircle,
  ANALYZING_STRUCTURE: LoaderCircle,
  CHUNKING_CODE: LoaderCircle,
  CLONING: LoaderCircle,
  COMPLETED: CheckCircle2,
  FAILED: XCircle,
  GENERATING_REPORT: LoaderCircle,
  GENERATING_SUMMARY: LoaderCircle,
  PENDING: CircleDashed,
  RUNNING_STATIC_ANALYSIS: LoaderCircle,
};

const ACTIVE_STATUSES = new Set<ReviewJobStatus>([
  "CLONING",
  "ANALYZING_STRUCTURE",
  "GENERATING_SUMMARY",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
]);

type SeverityBadgeProps = {
  className?: string;
  severity: IssueSeverity | string | null | undefined;
};

export function SeverityBadge({ className, severity }: SeverityBadgeProps) {
  const normalizedSeverity = normalizeSeverity(severity);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-xs font-semibold capitalize",
        normalizedSeverity
          ? SEVERITY_STYLES[normalizedSeverity]
          : "border-border bg-muted text-muted-foreground",
        className,
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          normalizedSeverity
            ? SEVERITY_DOT_STYLES[normalizedSeverity]
            : "bg-muted-foreground",
        )}
      />
      {normalizedSeverity ?? "unknown"}
    </span>
  );
}

type StatusBadgeProps = {
  className?: string;
  status: ReviewJobStatus;
};

export function StatusBadge({ className, status }: StatusBadgeProps) {
  const Icon = STATUS_ICONS[status] ?? CirclePlay;
  const isActive = ACTIVE_STATUSES.has(status);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs font-semibold text-muted-foreground",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-3.5", isActive && "animate-spin")}
      />
      {formatReviewJobStatus(status)}
    </span>
  );
}

export function formatReviewJobStatus(status: ReviewJobStatus) {
  const labels: Record<ReviewJobStatus, string> = {
    AI_REVIEWING: "Reviewing",
    ANALYZING_STRUCTURE: "Analyzing structure",
    CHUNKING_CODE: "Preparing code",
    CLONING: "Cloning",
    COMPLETED: "Completed",
    FAILED: "Failed",
    GENERATING_REPORT: "Generating report",
    GENERATING_SUMMARY: "Generating summary",
    PENDING: "Waiting",
    RUNNING_STATIC_ANALYSIS: "Static analysis",
  };

  return labels[status];
}

export function normalizeSeverity(
  severity: IssueSeverity | string | null | undefined,
): IssueSeverity | null {
  if (
    severity === "critical" ||
    severity === "high" ||
    severity === "medium" ||
    severity === "low" ||
    severity === "info"
  ) {
    return severity;
  }

  return null;
}
