"use client";

import {
  AlertTriangle,
  CirclePlay,
  GitFork,
  RefreshCw,
  ShieldCheck,
  Timer,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import { getRepositories } from "@/lib/repositories";
import { getReport } from "@/lib/reports";
import { getReviewJobs } from "@/lib/review-jobs";
import type { Repository } from "@/types/repository";
import type { ReviewReport, TopRiskyFile } from "@/types/report";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";

const ACTIVE_STATUSES = new Set<ReviewJobStatus>([
  "PENDING",
  "CLONING",
  "ANALYZING_STRUCTURE",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
]);

const STATUS_ORDER: ReviewJobStatus[] = [
  "PENDING",
  "CLONING",
  "ANALYZING_STRUCTURE",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
  "COMPLETED",
  "FAILED",
];

const SEVERITY_COLORS = {
  critical: "#f43f5e",
  high: "#fb923c",
  medium: "#facc15",
  low: "#60a5fa",
  info: "#94a3b8",
};

type DashboardState = {
  jobs: ReviewJob[];
  repositories: Repository[];
  reports: ReviewReport[];
};

export default function DashboardPage() {
  const [{ jobs, repositories, reports }, setDashboardState] =
    useState<DashboardState>({
      jobs: [],
      repositories: [],
      reports: [],
    });
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function loadDashboard() {
    setError(null);
    setIsLoading(true);

    try {
      const [repositoryData, jobData] = await Promise.all([
        getRepositories(),
        getReviewJobs(),
      ]);
      const completedJobs = jobData
        .filter((job) => job.status === "COMPLETED")
        .slice(0, 12);
      const reportResults = await Promise.allSettled(
        completedJobs.map((job) => getReport(job.id)),
      );
      const reportData = reportResults.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      );

      setDashboardState({
        jobs: jobData,
        repositories: repositoryData,
        reports: reportData,
      });
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, "Unable to load dashboard data."),
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  const activeReviewCount = jobs.filter((job) =>
    ACTIVE_STATUSES.has(job.status),
  ).length;
  const criticalIssueCount = sumReports(reports, "critical_count");
  const totalIssueCount = sumReports(reports, "total_issues");
  const averageRunTime = getAverageRunTime(jobs);
  const overallScore = getAverageScore(reports, "overall_score");
  const severityData = buildSeverityData(reports);
  const statusData = buildStatusData(jobs);
  const platformData = buildPlatformData(repositories);
  const riskyFiles = buildRiskyFiles(reports);
  const recentJobs = useMemo(() => jobs.slice(0, 6), [jobs]);
  const completedReviewCount = jobs.filter(
    (job) => job.status === "COMPLETED",
  ).length;
  const failedReviewCount = jobs.filter((job) => job.status === "FAILED").length;

  return (
    <>
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            AI source review workspace
          </p>
          <h1 className="mt-2 text-3xl font-extrabold tracking-normal">
            Dashboard
          </h1>
          <p className="mt-1 text-[15px] leading-6 text-muted-foreground">
            Security posture, review throughput, and repository coverage across
            the current workspace.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={isLoading}
            onClick={() => void loadDashboard()}
            variant="secondary"
          >
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
          <Button asChild>
            <Link href="/repositories">
              <GitFork aria-hidden="true" />
              Add Repository
            </Link>
          </Button>
        </div>
      </header>

      {error ? (
        <Card>
          <CardContent className="p-5">
            <p className="text-[15px] text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          icon={GitFork}
          isLoading={isLoading}
          label="Repositories"
          value={repositories.length.toString()}
        />
        <MetricCard
          icon={CirclePlay}
          isLoading={isLoading}
          label="Active reviews"
          value={activeReviewCount.toString()}
        />
        <MetricCard
          icon={ShieldCheck}
          isLoading={isLoading}
          label="Completed"
          value={completedReviewCount.toString()}
        />
        <MetricCard
          icon={AlertTriangle}
          isLoading={isLoading}
          label="Critical issues"
          value={criticalIssueCount.toString()}
        />
        <MetricCard
          icon={Timer}
          isLoading={isLoading}
          label="Avg. run time"
          value={averageRunTime}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card>
          <CardHeader>
            <CardTitle>Security Posture</CardTitle>
            <CardDescription>
              Average score and severity mix from the latest completed reports.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 lg:grid-cols-[260px_1fr] lg:items-center">
            <ScoreGauge value={overallScore} />
            <SeverityBars data={severityData} totalIssues={totalIssueCount} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Review Status</CardTitle>
            <CardDescription>
              Worker queue distribution across active and terminal states.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <StatusBars data={statusData} />
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <Card>
          <CardHeader>
            <CardTitle>Repository Coverage</CardTitle>
            <CardDescription>
              Connected source platforms and report coverage.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            <PlatformBars data={platformData} />
            <div className="rounded-md border border-border bg-background p-4">
              <p className="text-sm font-medium uppercase text-muted-foreground">
                Report coverage
              </p>
              <p className="mt-2 text-3xl font-extrabold">
                {reports.length}/{Math.max(completedReviewCount, 0)}
              </p>
              <p className="mt-1 text-[15px] text-muted-foreground">
                Completed reviews with a generated report loaded into the
                dashboard.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader>
            <CardTitle>Recent Review Jobs</CardTitle>
            <CardDescription>
              Latest worker activity with quick access to job details.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <RecentJobsTable jobs={recentJobs} />
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Risk Hotspots</CardTitle>
            <CardDescription>
              Files appearing most often in recent reports.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <RiskyFiles files={riskyFiles} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Pipeline Snapshot</CardTitle>
            <CardDescription>
              Current worker stages expected in the realtime SSE tracker.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PipelineSnapshot failedCount={failedReviewCount} jobs={jobs} />
          </CardContent>
        </Card>
      </section>
    </>
  );
}

function MetricCard({
  icon: Icon,
  isLoading,
  label,
  value,
}: {
  icon: typeof GitFork;
  isLoading: boolean;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-5">
        <div>
          <p className="text-sm font-medium uppercase text-muted-foreground">
            {label}
          </p>
          {isLoading ? (
            <div className="mt-3 h-9 w-20 animate-pulse rounded bg-muted" />
          ) : (
            <p className="mt-2 text-4xl font-extrabold tracking-normal">
              {value}
            </p>
          )}
        </div>
        <span className="flex size-11 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Icon aria-hidden="true" className="size-5" />
        </span>
      </CardContent>
    </Card>
  );
}

function ScoreGauge({ value }: { value: number | null }) {
  const score = value ?? 0;
  const percentage = Math.max(0, Math.min(100, score * 10));
  const radius = 46;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;
  const color = score < 5 ? "#f43f5e" : score < 8 ? "#f59e0b" : "#10b981";

  return (
    <div className="flex items-center gap-5">
      <svg className="size-36 -rotate-90" viewBox="0 0 120 120">
        <circle
          cx="60"
          cy="60"
          fill="none"
          r={radius}
          stroke="hsl(var(--muted))"
          strokeWidth="12"
        />
        <circle
          cx="60"
          cy="60"
          fill="none"
          r={radius}
          stroke={color}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          strokeLinecap="round"
          strokeWidth="12"
        />
      </svg>
      <div>
        <p className="text-sm font-medium uppercase text-muted-foreground">
          Overall score
        </p>
        <p className="mt-1 text-4xl font-extrabold tracking-normal">
          {value === null ? "--" : score.toFixed(1)}
        </p>
        <p className="text-[15px] text-muted-foreground">/ 10.0</p>
      </div>
    </div>
  );
}

function SeverityBars({
  data,
  totalIssues,
}: {
  data: Array<{ color: string; label: string; value: number }>;
  totalIssues: number;
}) {
  const maxValue = Math.max(...data.map((item) => item.value), 1);

  return (
    <div className="grid gap-3">
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium uppercase text-muted-foreground">
            Issue distribution
          </p>
          <p className="mt-1 text-[15px] text-muted-foreground">
            {totalIssues} findings across loaded reports
          </p>
        </div>
      </div>
      {data.map((item) => (
        <div className="grid gap-2" key={item.label}>
          <div className="flex items-center justify-between gap-3">
            <span className="capitalize text-[15px] text-muted-foreground">
              {item.label}
            </span>
            <span className="font-semibold">{item.value}</span>
          </div>
          <div className="h-3 overflow-hidden rounded bg-muted">
            <div
              className="h-full rounded"
              style={{
                backgroundColor: item.color,
                width: `${(item.value / maxValue) * 100}%`,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function StatusBars({
  data,
}: {
  data: Array<{ label: ReviewJobStatus; value: number }>;
}) {
  const visibleData = data.filter((item) => item.value > 0);
  const maxValue = Math.max(...visibleData.map((item) => item.value), 1);

  if (visibleData.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No review jobs yet.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {visibleData.map((item) => (
        <div className="grid gap-2" key={item.label}>
          <div className="flex items-center justify-between gap-3">
            <span className="text-[15px] text-muted-foreground">
              {formatStatus(item.label)}
            </span>
            <span className="font-semibold">{item.value}</span>
          </div>
          <div className="h-3 overflow-hidden rounded bg-muted">
            <div
              className="h-full rounded bg-slate-300"
              style={{ width: `${(item.value / maxValue) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function PlatformBars({
  data,
}: {
  data: Array<{ label: string; value: number }>;
}) {
  const maxValue = Math.max(...data.map((item) => item.value), 1);

  return (
    <div className="grid gap-3">
      {data.map((item) => (
        <div className="grid gap-2" key={item.label}>
          <div className="flex items-center justify-between">
            <span className="capitalize text-[15px] text-muted-foreground">
              {item.label}
            </span>
            <span className="font-semibold">{item.value}</span>
          </div>
          <div className="h-3 overflow-hidden rounded bg-muted">
            <div
              className="h-full rounded bg-slate-300"
              style={{ width: `${(item.value / maxValue) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function RecentJobsTable({ jobs }: { jobs: ReviewJob[] }) {
  if (jobs.length === 0) {
    return (
      <div className="border-t border-border p-5 text-[15px] text-muted-foreground">
        No review jobs have been created yet.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto border-t border-border">
      <table className="w-full min-w-[720px] text-left text-[15px]">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-5 py-3 font-medium">Repository</th>
            <th className="px-5 py-3 font-medium">Status</th>
            <th className="px-5 py-3 font-medium">Branch</th>
            <th className="px-5 py-3 font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              className="border-t border-border transition-colors hover:bg-muted/35"
              key={job.id}
            >
              <td className="px-5 py-4">
                <Link
                  className="font-semibold text-foreground hover:text-primary/80"
                  href={`/reviews/${job.id}`}
                >
                  {job.repository_name ?? job.repository_id}
                </Link>
              </td>
              <td className="px-5 py-4">
                <span className="rounded-md border border-border bg-background px-2 py-1 text-xs font-semibold">
                  {formatStatus(job.status)}
                </span>
              </td>
              <td className="px-5 py-4 text-muted-foreground">
                {job.branch ?? "main"}
              </td>
              <td className="px-5 py-4 text-muted-foreground">
                {formatDate(job.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RiskyFiles({ files }: { files: TopRiskyFile[] }) {
  if (files.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No risky files available until completed reports are generated.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {files.map((file) => (
        <Link
          className="block rounded-md border border-border bg-background p-4 transition-colors hover:bg-muted/35"
          href={
            file.job_id
              ? `/reviews/${file.job_id}/issues?file_path=${encodeURIComponent(file.path)}`
              : "/reviews"
          }
          key={`${file.job_id ?? "unknown"}-${file.path}`}
        >
          <div className="flex items-start justify-between gap-4">
            <p className="break-all text-[15px] font-semibold">{file.path}</p>
            <span className="rounded-md border border-border px-2 py-1 text-xs font-semibold">
              {file.issue_count}
            </span>
          </div>
          <p className="mt-2 text-sm capitalize text-muted-foreground">
            Max severity: {file.max_severity ?? "unknown"}
          </p>
        </Link>
      ))}
    </div>
  );
}

function PipelineSnapshot({
  failedCount,
  jobs,
}: {
  failedCount: number;
  jobs: ReviewJob[];
}) {
  const stages = [
    {
      label: "Queued",
      value: jobs.filter((job) => job.status === "PENDING").length,
    },
    {
      label: "Static analysis",
      value: jobs.filter((job) => job.status === "RUNNING_STATIC_ANALYSIS")
        .length,
    },
    {
      label: "AI reviewing",
      value: jobs.filter((job) => job.status === "AI_REVIEWING").length,
    },
    {
      label: "Generating report",
      value: jobs.filter((job) => job.status === "GENERATING_REPORT").length,
    },
    {
      label: "Failed",
      value: failedCount,
    },
  ];

  return (
    <div className="grid gap-3">
      {stages.map((stage) => (
        <div
          className="flex items-center justify-between rounded-md border border-border bg-background px-4 py-3"
          key={stage.label}
        >
          <span className="text-[15px] text-muted-foreground">
            {stage.label}
          </span>
          <span className="font-semibold">{stage.value}</span>
        </div>
      ))}
    </div>
  );
}

function buildSeverityData(reports: ReviewReport[]) {
  return [
    {
      color: SEVERITY_COLORS.critical,
      label: "critical",
      value: sumReports(reports, "critical_count"),
    },
    {
      color: SEVERITY_COLORS.high,
      label: "high",
      value: sumReports(reports, "high_count"),
    },
    {
      color: SEVERITY_COLORS.medium,
      label: "medium",
      value: sumReports(reports, "medium_count"),
    },
    {
      color: SEVERITY_COLORS.low,
      label: "low",
      value: sumReports(reports, "low_count"),
    },
    {
      color: SEVERITY_COLORS.info,
      label: "info",
      value: sumReports(reports, "info_count"),
    },
  ];
}

function buildStatusData(jobs: ReviewJob[]) {
  return STATUS_ORDER.map((status) => ({
    label: status,
    value: jobs.filter((job) => job.status === status).length,
  }));
}

function buildPlatformData(repositories: Repository[]) {
  return ["github", "gitlab", "unknown"].map((platform) => ({
    label: platform,
    value: repositories.filter(
      (repository) => (repository.platform ?? "unknown") === platform,
    ).length,
  }));
}

function buildRiskyFiles(reports: ReviewReport[]) {
  const fileMap = new Map<string, TopRiskyFile>();

  for (const report of reports) {
    for (const file of report.top_risky_files ?? []) {
      const existingFile = fileMap.get(file.path);

      fileMap.set(file.path, {
        path: file.path,
        issue_count: (existingFile?.issue_count ?? 0) + file.issue_count,
        max_severity: existingFile?.max_severity ?? file.max_severity,
        job_id: existingFile?.job_id ?? report.job_id,
      });
    }
  }

  return Array.from(fileMap.values())
    .sort((leftFile, rightFile) => rightFile.issue_count - leftFile.issue_count)
    .slice(0, 5);
}

function sumReports(
  reports: ReviewReport[],
  field:
    | "critical_count"
    | "high_count"
    | "info_count"
    | "low_count"
    | "medium_count"
    | "total_issues",
) {
  return reports.reduce((sum, report) => sum + report[field], 0);
}

function getAverageScore(
  reports: ReviewReport[],
  field: "overall_score" | "performance_score" | "security_score",
) {
  const scores = reports
    .map((report) => report[field])
    .filter((score): score is number => score !== null);

  if (scores.length === 0) {
    return null;
  }

  return scores.reduce((sum, score) => sum + score, 0) / scores.length;
}

function getAverageRunTime(jobs: ReviewJob[]) {
  const durations = jobs.flatMap((job) => {
    if (!job.started_at || !job.completed_at) {
      return [];
    }

    const durationMs =
      new Date(job.completed_at).getTime() - new Date(job.started_at).getTime();

    return durationMs > 0 ? [durationMs] : [];
  });

  if (durations.length === 0) {
    return "--";
  }

  const averageMs =
    durations.reduce((sum, durationMs) => sum + durationMs, 0) / durations.length;

  return formatDuration(averageMs);
}

function formatDuration(durationMs: number) {
  const totalMinutes = Math.max(1, Math.round(durationMs / 60_000));

  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }

  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatStatus(status: ReviewJobStatus) {
  return status.replaceAll("_", " ");
}
