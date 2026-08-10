import type { Repository } from "@/types/repository";
import type { ReviewReport } from "@/types/report";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";

export const ACTIVE_STATUSES = new Set<ReviewJobStatus>([
  "PENDING",
  "CLONING",
  "ANALYZING_STRUCTURE",
  "GENERATING_SUMMARY",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
]);

const RECENT_REPORT_LIMIT = 12;

export type SeverityKey = "critical" | "high" | "medium" | "low" | "info";

export type DashboardData = {
  repositories: Repository[];
  jobs: ReviewJob[];
  reports: ReviewReport[];
  reportLoadFailureJobIds: string[];
  reportRequestCount: number;
};

export type PostureTone =
  | "critical"
  | "high"
  | "attention"
  | "clear"
  | "unavailable"
  | "running"
  | "pending";

export type RiskPosture = {
  tone: PostureTone;
  title: string;
  message: string;
  criticalCount: number;
  highCount: number;
  totalIssues: number;
  reposReviewed: number;
  reportCount: number;
};

export type PrimaryAction = {
  label: string;
  href: string;
};

export type AttentionTone =
  | "critical"
  | "high"
  | "failed"
  | "report"
  | "file";

export type AttentionItem = {
  id: string;
  tone: AttentionTone;
  title: string;
  meta: string;
  href: string;
  count: number | null;
};

export type ActiveReview = {
  id: string;
  repositoryName: string;
  branch: string;
  status: ReviewJobStatus;
  startedAt: string;
  isQueued: boolean;
  href: string;
};

export type RecentReview = {
  id: string;
  repositoryName: string;
  status: ReviewJobStatus;
  branch: string;
  totalIssues: number | null;
  date: string;
  href: string;
};

export type RepositoryOverviewItem = {
  id: string;
  name: string;
  platform: string;
  defaultBranch: string;
  lastReviewedAt: string | null;
  hasRecentReview: boolean;
  href: string;
};

export type LatestReportSummary = {
  jobId: string;
  repositoryName: string;
  createdAt: string;
  severity: Array<{ key: SeverityKey; value: number }>;
  totalIssues: number;
  executiveSummary: string | null;
  href: string;
};

const SEVERITY_FIELDS: Array<{ key: SeverityKey; field: keyof ReviewReport }> = [
  { key: "critical", field: "critical_count" },
  { key: "high", field: "high_count" },
  { key: "medium", field: "medium_count" },
  { key: "low", field: "low_count" },
  { key: "info", field: "info_count" },
];

export function isActiveStatus(status: ReviewJobStatus) {
  return ACTIVE_STATUSES.has(status);
}

export function sumReports(
  reports: ReviewReport[],
  field:
    | "critical_count"
    | "high_count"
    | "info_count"
    | "low_count"
    | "medium_count"
    | "total_findings",
) {
  return reports.reduce((total, report) => total + report[field], 0);
}

function getReviewJobTimestamp(job: ReviewJob) {
  return new Date(job.completed_at ?? job.started_at ?? job.created_at).getTime();
}

export function compareReviewJobsByLatest(left: ReviewJob, right: ReviewJob) {
  return getReviewJobTimestamp(right) - getReviewJobTimestamp(left);
}

export function selectRecentCompletedJobs(
  jobs: ReviewJob[],
  limit = RECENT_REPORT_LIMIT,
) {
  return [...jobs]
    .filter((job) => job.status === "COMPLETED")
    .sort(compareReviewJobsByLatest)
    .slice(0, limit);
}

export function hasIncompleteReportData(data: DashboardData) {
  return (
    data.reportLoadFailureJobIds.length > 0 ||
    data.reports.length < data.reportRequestCount
  );
}

function buildRepositoryNameByJob(jobs: ReviewJob[]) {
  const map = new Map<string, string>();
  for (const job of jobs) {
    map.set(job.id, job.repository_name ?? job.repository_id);
  }
  return map;
}

export function buildRiskPosture(data: DashboardData): RiskPosture {
  const { repositories, jobs, reports } = data;
  const runningCount = jobs.filter(
    (job) => job.status !== "PENDING" && ACTIVE_STATUSES.has(job.status),
  ).length;
  const queuedCount = jobs.filter((job) => job.status === "PENDING").length;
  const completedCount = jobs.filter((job) => job.status === "COMPLETED").length;
  const criticalCount = sumReports(reports, "critical_count");
  const highCount = sumReports(reports, "high_count");
  const totalIssues = sumReports(reports, "total_findings");
  const reposReviewed = repositories.filter(
    (repository) => repository.last_reviewed_at !== null,
  ).length;
  const reportCount = reports.length;
  const isReportDataIncomplete = hasIncompleteReportData(data);
  const expectedReportCount =
    data.reportRequestCount > 0
      ? data.reportRequestCount
      : Math.min(completedCount, RECENT_REPORT_LIMIT);
  const coverageWarning = isReportDataIncomplete
    ? ` Report coverage is incomplete (${reportCount}/${expectedReportCount} loaded), so totals may be understated.`
    : "";

  const base = {
    criticalCount,
    highCount,
    totalIssues,
    reposReviewed,
    reportCount,
  };

  if (completedCount === 0) {
    if (runningCount > 0) {
      return {
        ...base,
        tone: "running",
        title: "Your first review is in progress",
        message:
          "A review is running now. Your security posture will appear as soon as it completes.",
      };
    }

    if (queuedCount > 0) {
      return {
        ...base,
        tone: "pending",
        title: "Your first review is queued",
        message:
          "The review is waiting to start. Your security posture will appear after it completes.",
      };
    }

    return {
      ...base,
      tone: "pending",
      title: "No completed reviews yet",
      message:
        "Start a review on a connected repository to generate your first security posture.",
    };
  }

  if (reportCount === 0) {
    return {
      ...base,
      tone: "unavailable",
      title: "Security posture is unavailable",
      message:
        "Completed reviews exist, but their reports could not be loaded. Retry before relying on this dashboard.",
    };
  }

  if (criticalCount > 0) {
    return {
      ...base,
      tone: "critical",
      title: "Critical findings require attention",
      message: `${formatCount(criticalCount, "critical finding")} across your ${formatCount(
        reportCount,
        "recent report",
      )} need immediate triage.${coverageWarning}`,
    };
  }

  if (highCount > 0) {
    return {
      ...base,
      tone: "high",
      title: "High-severity findings need review",
      message: `${formatCount(highCount, "high-severity finding")} across your ${formatCount(
        reportCount,
        "recent report",
      )} should be reviewed soon.${coverageWarning}`,
    };
  }

  if (isReportDataIncomplete) {
    return {
      ...base,
      tone: "unavailable",
      title: "Security posture is incomplete",
      message:
        totalIssues > 0
          ? `${formatCount(totalIssues, "finding")} are visible in the reports that loaded.${coverageWarning}`
          : `Some recent reports could not be loaded.${coverageWarning}`,
    };
  }

  if (totalIssues > 0) {
    return {
      ...base,
      tone: "attention",
      title: "Findings are ready for triage",
      message: `${formatCount(totalIssues, "finding")} were detected in your ${formatCount(
        reportCount,
        "recent report",
      )}. No critical or high severity issues.`,
    };
  }

  return {
    ...base,
    tone: "clear",
    title: "Your latest reviews are clear",
    message: `No findings in your ${formatCount(
      reportCount,
      "most recent report",
    )}. Keep reviews running to stay covered.`,
  };
}

export function buildPrimaryAction(data: DashboardData): PrimaryAction {
  const { repositories, jobs } = data;

  if (repositories.length === 0) {
    return { label: "Connect a repository", href: "/repositories" };
  }

  const activeCount = jobs.filter((job) => ACTIVE_STATUSES.has(job.status)).length;
  if (activeCount > 0) {
    return { label: "View active reviews", href: "/reviews" };
  }

  const completedCount = jobs.filter((job) => job.status === "COMPLETED").length;
  if (completedCount === 0) {
    return { label: "Start your first review", href: "/repositories" };
  }

  const latestReportWithFindings = getLatestReportContext(
    data,
    (report) => report.total_findings > 0,
  );
  if (latestReportWithFindings) {
    return {
      label: "Review findings",
      href: `/reviews/${latestReportWithFindings.jobId}/issues`,
    };
  }

  if (hasIncompleteReportData(data)) {
    return { label: "Open completed reviews", href: "/reviews" };
  }

  const latestReport = getLatestReportContext(data);

  return {
    label: "Open latest report",
    href: latestReport ? `/reviews/${latestReport.jobId}/report` : "/reviews",
  };
}

export function buildNeedsAttention(
  data: DashboardData,
  limit = 6,
): AttentionItem[] {
  const { jobs, reports, reportLoadFailureJobIds } = data;
  const repositoryNameByJob = buildRepositoryNameByJob(jobs);
  const items: AttentionItem[] = [];

  const criticalReports = reports
    .filter((report) => report.critical_count > 0)
    .sort(
      (left, right) =>
        right.critical_count - left.critical_count ||
        new Date(right.created_at).getTime() -
          new Date(left.created_at).getTime(),
    );
  for (const report of criticalReports) {
    const name = repositoryNameByJob.get(report.job_id) ?? "Repository";
    items.push({
      id: `critical:${report.job_id}`,
      tone: "critical",
      title: `${formatCount(report.critical_count, "critical finding")} in ${name}`,
      meta: "Highest severity. Triage before shipping.",
      href: `/reviews/${report.job_id}/issues`,
      count: report.critical_count,
    });
  }

  const highReports = reports
    .filter((report) => report.high_count > 0)
    .sort(
      (left, right) =>
        right.high_count - left.high_count ||
        new Date(right.created_at).getTime() -
          new Date(left.created_at).getTime(),
    );
  for (const report of highReports) {
    const name = repositoryNameByJob.get(report.job_id) ?? "Repository";
    items.push({
      id: `high:${report.job_id}`,
      tone: "high",
      title: `${formatCount(report.high_count, "high-severity finding")} in ${name}`,
      meta: "High severity. Review soon.",
      href: `/reviews/${report.job_id}/issues`,
      count: report.high_count,
    });
  }

  const failedJobs = [...jobs]
    .filter((job) => job.status === "FAILED")
    .sort(compareReviewJobsByLatest);
  for (const job of failedJobs) {
    items.push({
      id: `failed:${job.id}`,
      tone: "failed",
      title: `${job.repository_name ?? job.repository_id} review failed`,
      meta: job.error_message?.trim()
        ? job.error_message
        : "The review did not complete. Open it to retry.",
      href: `/reviews/${job.id}`,
      count: null,
    });
  }

  if (reportLoadFailureJobIds.length > 0) {
    items.push({
      id: "report-load-failures",
      tone: "report",
      title: `${formatCount(reportLoadFailureJobIds.length, "completed report")} unavailable`,
      meta: "Retry the dashboard before relying on posture totals.",
      href: "/reviews",
      count: reportLoadFailureJobIds.length,
    });
  }

  const riskyFiles = reports
    .flatMap((report) =>
      (report.top_risky_files ?? []).map((file) => ({
        jobId: report.job_id,
        path: file.path,
        issueCount: file.issue_count,
      })),
    )
    .sort((left, right) => {
      if (right.issueCount !== left.issueCount) {
        return right.issueCount - left.issueCount;
      }
      return left.path.localeCompare(right.path);
    });
  for (const file of riskyFiles) {
    items.push({
      id: `file:${file.jobId}:${file.path}`,
      tone: "file",
      title: file.path,
      meta: `Most flagged file - ${formatCount(file.issueCount, "issue")}`,
      href: `/reviews/${file.jobId}/issues?file_path=${encodeURIComponent(file.path)}`,
      count: file.issueCount,
    });
  }

  return items.slice(0, limit);
}

export function buildActiveReviews(data: DashboardData): ActiveReview[] {
  return [...data.jobs]
    .filter((job) => ACTIVE_STATUSES.has(job.status))
    .sort(compareReviewJobsByLatest)
    .map((job) => ({
      id: job.id,
      repositoryName: job.repository_name ?? job.repository_id,
      branch: job.branch ?? "main",
      status: job.status,
      startedAt: job.started_at ?? job.created_at,
      isQueued: job.status === "PENDING",
      href: `/reviews/${job.id}`,
    }));
}

export function buildRecentReviews(
  data: DashboardData,
  limit = 5,
): RecentReview[] {
  const reportByJob = new Map(
    data.reports.map((report) => [report.job_id, report]),
  );

  return [...data.jobs]
    .sort(compareReviewJobsByLatest)
    .slice(0, limit)
    .map((job) => {
      const report = reportByJob.get(job.id) ?? null;
      const hasReport = job.status === "COMPLETED" && report !== null;

      return {
        id: job.id,
        repositoryName: job.repository_name ?? job.repository_id,
        status: job.status,
        branch: job.branch ?? "main",
        totalIssues: report?.total_findings ?? null,
        date: job.completed_at ?? job.created_at,
        href: hasReport ? `/reviews/${job.id}/report` : `/reviews/${job.id}`,
      };
    });
}

export function buildRepositoryOverview(
  data: DashboardData,
  limit = 5,
): RepositoryOverviewItem[] {
  return [...data.repositories]
    .sort((left, right) => {
      const leftTime = left.last_reviewed_at
        ? new Date(left.last_reviewed_at).getTime()
        : new Date(left.created_at).getTime();
      const rightTime = right.last_reviewed_at
        ? new Date(right.last_reviewed_at).getTime()
        : new Date(right.created_at).getTime();
      return rightTime - leftTime;
    })
    .slice(0, limit)
    .map((repository) => ({
      id: repository.id,
      name: repository.name,
      platform: repository.platform ?? "other",
      defaultBranch: repository.default_branch,
      lastReviewedAt: repository.last_reviewed_at,
      hasRecentReview: repository.last_reviewed_at !== null,
      href: `/repositories/${repository.id}`,
    }));
}

function getLatestReportContext(
  data: DashboardData,
  predicate: (report: ReviewReport) => boolean = () => true,
) {
  const reportByJob = new Map(
    data.reports.map((report) => [report.job_id, report]),
  );

  const latestJob = [...data.jobs]
    .filter((job) => {
      const report = reportByJob.get(job.id);
      return (
        job.status === "COMPLETED" &&
        report !== undefined &&
        predicate(report)
      );
    })
    .sort(compareReviewJobsByLatest)[0];

  if (!latestJob) {
    return null;
  }

  const report = reportByJob.get(latestJob.id);
  if (!report) {
    return null;
  }

  return { jobId: latestJob.id, report, job: latestJob };
}

export function buildLatestReportSummary(
  data: DashboardData,
): LatestReportSummary | null {
  const context = getLatestReportContext(data);
  if (!context) {
    return null;
  }

  const { report, job } = context;

  return {
    jobId: report.job_id,
    repositoryName: job.repository_name ?? job.repository_id,
    createdAt: report.created_at,
    severity: SEVERITY_FIELDS.map(({ key, field }) => ({
      key,
      value: report[field] as number,
    })),
    totalIssues: report.total_findings,
    executiveSummary: report.executive_summary,
    href: `/reviews/${report.job_id}/report`,
  };
}

export function formatCount(value: number, noun: string) {
  return `${value} ${noun}${value === 1 ? "" : "s"}`;
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatRelativeTime(value: string) {
  const target = new Date(value).getTime();
  const diffMs = Date.now() - target;
  const diffMinutes = Math.round(diffMs / 60000);

  if (Number.isNaN(diffMinutes)) {
    return "";
  }

  if (Math.abs(diffMinutes) < 1) {
    return "just now";
  }

  const units: Array<{ limit: number; divisor: number; unit: Intl.RelativeTimeFormatUnit }> = [
    { limit: 60, divisor: 1, unit: "minute" },
    { limit: 1440, divisor: 60, unit: "hour" },
    { limit: 43200, divisor: 1440, unit: "day" },
    { limit: 525600, divisor: 43200, unit: "month" },
    { limit: Number.POSITIVE_INFINITY, divisor: 525600, unit: "year" },
  ];

  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const absMinutes = Math.abs(diffMinutes);

  for (const { limit, divisor, unit } of units) {
    if (absMinutes < limit) {
      return formatter.format(-Math.round(diffMinutes / divisor), unit);
    }
  }

  return formatter.format(-Math.round(diffMinutes / 525600), "year");
}
