"use client";

import {
  AlertTriangle,
  CirclePlay,
  Clock,
  FileWarning,
  GitFork,
  RefreshCw,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { StatusBadge } from "@/components/reviews/review-badges";
import { ScoreTrack } from "@/components/reviews/score-track";
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
import type { ReviewReport } from "@/types/report";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";

const ACTIVE_STATUSES = new Set<ReviewJobStatus>([
  "PENDING",
  "CLONING",
  "ANALYZING_STRUCTURE",
  "GENERATING_SUMMARY",
  "RUNNING_STATIC_ANALYSIS",
  "CHUNKING_CODE",
  "AI_REVIEWING",
  "GENERATING_REPORT",
]);

const SEVERITY_COLORS = {
  critical: "#e11d48",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#38bdf8",
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
      const completedJobs = [...jobData]
        .filter((job) => job.status === "COMPLETED")
        .sort(compareReviewJobsByLatest)
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
  const completedReviewCount = jobs.filter(
    (job) => job.status === "COMPLETED",
  ).length;
  const criticalIssueCount = sumReports(reports, "critical_count");
  const highIssueCount = sumReports(reports, "high_count");
  const totalIssueCount = sumReports(reports, "total_issues");
  const overallScore = getAverageScore(reports, "overall_score");
  const severityData = buildSeverityData(reports);
  const jobStateData = buildJobStateData(jobs);
  const platformData = buildPlatformData(repositories);
  const riskHotspots = buildRiskHotspots(reports);
  const recentJobs = useMemo(
    () => [...jobs].sort(compareReviewJobsByLatest).slice(0, 6),
    [jobs],
  );
  const averageRunTime = getAverageRunTime(jobs);
  const latestCompletedJob = useMemo(
    () => getLatestCompletedReviewJob(jobs),
    [jobs],
  );
  const summary = getDashboardSummary({
    activeReviewCount,
    completedReviewCount,
    criticalIssueCount,
    highIssueCount,
    repositoryCount: repositories.length,
    reportCount: reports.length,
    totalIssueCount,
  });
  const nextStep = getNextStep({
    activeReviewCount,
    completedReviewCount,
    criticalIssueCount,
    highIssueCount,
    latestCompletedJobId: latestCompletedJob?.id ?? null,
    repositoryCount: repositories.length,
    totalIssueCount,
  });

  return (
    <>
      <div className="flex flex-wrap justify-end gap-2">
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

      {error ? (
        <Card>
          <CardContent className="p-5">
            <p className="text-[15px] text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      <section className="grid gap-4 2xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <DashboardSummaryCard
          isLoading={isLoading}
          metrics={[
            {
              icon: GitFork,
              label: "Repositories",
              value: repositories.length.toString(),
            },
            {
              icon: CirclePlay,
              label: "Running",
              value: activeReviewCount.toString(),
            },
            {
              icon: ShieldCheck,
              label: "Completed",
              value: completedReviewCount.toString(),
            },
            {
              icon: AlertTriangle,
              label: "Findings",
              value: totalIssueCount.toString(),
            },
            {
              icon: Clock,
              label: "Avg. run time",
              value: formatDuration(averageRunTime),
            },
          ]}
          summary={summary}
        />
        <NextStepCard isLoading={isLoading} nextStep={nextStep} />
      </section>

      <section>
        <Card>
          <CardHeader>
            <CardTitle>Latest Review Results</CardTitle>
            <CardDescription>
              Based on the latest completed review reports.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 lg:grid-cols-[minmax(240px,320px)_1fr]">
            <div className="grid gap-4 rounded-md border border-border bg-background p-4">
              <ScoreTrack
                caption={
                  reports.length === 0
                    ? "No completed reports loaded"
                    : `${reports.length} recent reports loaded`
                }
                label="Overall score"
                showNoFindings={reports.length > 0 && totalIssueCount === 0}
                value={overallScore}
              />
              <div className="grid grid-cols-2 gap-3 border-t border-border pt-4">
                <MiniStat label="Findings" value={totalIssueCount.toString()} />
                <MiniStat
                  label="Critical"
                  value={criticalIssueCount.toString()}
                />
              </div>
            </div>
            <SeverityBars data={severityData} totalIssues={totalIssueCount} />
          </CardContent>
        </Card>
      </section>

      <section>
        <Card className="overflow-hidden">
          <CardHeader>
            <CardTitle>Recent Reviews</CardTitle>
            <CardDescription>
              Open a review to see the report, issues, and execution details.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <RecentJobsTable jobs={recentJobs} />
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[0.8fr_1.1fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Review Jobs</CardTitle>
            <CardDescription>
              A simple split of waiting, running, completed, and failed jobs.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <StatusBars data={jobStateData} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Repositories</CardTitle>
            <CardDescription>
              Connected sources and completed reviews with reports.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5 lg:grid-cols-[1fr_260px]">
            <PlatformBars data={platformData} />
            <div className="rounded-md border border-border bg-background p-4">
              <p className="text-sm font-medium uppercase text-muted-foreground">
                Reports ready
              </p>
              <p className="mt-2 text-3xl font-extrabold tracking-normal">
                {formatReportReadinessValue(reports.length, completedReviewCount)}
              </p>
              <p className="mt-1 text-[15px] text-muted-foreground">
                {formatReportReadinessDescription(
                  reports.length,
                  completedReviewCount,
                )}
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Risk Hotspots</CardTitle>
            <CardDescription>
              Files most frequently flagged in recent completed reports.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <RiskHotspotsList hotspots={riskHotspots} />
          </CardContent>
        </Card>
      </section>
    </>
  );
}

type DashboardMetric = {
  icon: LucideIcon;
  label: string;
  value: string;
};

type DashboardSummary = {
  description: string;
  icon: LucideIcon;
  title: string;
};

type NextStep = {
  description: string;
  href: string;
  label: string;
  title: string;
};

function DashboardSummaryCard({
  isLoading,
  metrics,
  summary,
}: {
  isLoading: boolean;
  metrics: DashboardMetric[];
  summary: DashboardSummary;
}) {
  const Icon = summary.icon;

  return (
    <Card>
      <CardContent className="grid gap-6 p-6">
        <div className="flex gap-4">
          <span className="flex size-12 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
            <Icon aria-hidden="true" className="size-5" />
          </span>
          <div className="min-w-0">
            {isLoading ? (
              <div className="h-8 w-64 max-w-full animate-pulse rounded bg-muted" />
            ) : (
              <h2 className="text-2xl font-extrabold tracking-normal">
                {summary.title}
              </h2>
            )}
            <p className="mt-2 text-[15px] leading-6 text-muted-foreground">
              {isLoading ? "Loading the latest workspace data." : summary.description}
            </p>
          </div>
        </div>

        <div className="grid overflow-hidden rounded-md border border-border bg-border [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
          {metrics.map((metric) => (
            <SummaryMetric
              icon={metric.icon}
              isLoading={isLoading}
              key={metric.label}
              label={metric.label}
              value={metric.value}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SummaryMetric({
  icon: Icon,
  isLoading,
  label,
  value,
}: {
  icon: LucideIcon;
  isLoading: boolean;
  label: string;
  value: string;
}) {
  return (
    <div className="grid min-h-28 gap-3 bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium uppercase text-muted-foreground">
          {label}
        </p>
        <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Icon aria-hidden="true" className="size-4" />
        </span>
      </div>
      <div className="min-w-0">
        {isLoading ? (
          <div className="mt-3 h-8 w-20 animate-pulse rounded bg-muted" />
        ) : (
          <p className="whitespace-nowrap text-3xl font-extrabold tracking-normal tabular-nums">
            {value}
          </p>
        )}
      </div>
    </div>
  );
}

function NextStepCard({
  isLoading,
  nextStep,
}: {
  isLoading: boolean;
  nextStep: NextStep;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Next Step</CardTitle>
        <CardDescription>The most useful place to go from here.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {isLoading ? (
          <>
            <div className="h-6 w-52 animate-pulse rounded bg-muted" />
            <div className="h-16 animate-pulse rounded bg-muted" />
          </>
        ) : (
          <>
            <div>
              <h2 className="text-xl font-semibold tracking-normal">
                {nextStep.title}
              </h2>
              <p className="mt-2 text-[15px] leading-6 text-muted-foreground">
                {nextStep.description}
              </p>
            </div>
            <Button asChild>
              <Link href={nextStep.href}>{nextStep.label}</Link>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-2xl font-extrabold tracking-normal">{value}</p>
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

  if (totalIssues === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No findings in the latest review reports.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      <SeverityStackedBar data={data} totalIssues={totalIssues} />
      <div>
        <p className="text-sm font-medium uppercase text-muted-foreground">
          Findings by severity
        </p>
        <p className="mt-1 text-[15px] text-muted-foreground">
          {totalIssues} findings across latest completed reports
        </p>
      </div>
      {data.map((item) => (
        <div className="grid gap-2" key={item.label}>
          <div className="flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 capitalize text-[15px] text-muted-foreground">
              <span
                className="size-2.5 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              {item.label}
            </span>
            <span className="font-semibold">
              {item.value}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {formatPercent(item.value, totalIssues)}
              </span>
            </span>
          </div>
          <div className="h-2.5 overflow-hidden rounded bg-muted">
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

function SeverityStackedBar({
  data,
  totalIssues,
}: {
  data: Array<{ color: string; label: string; value: number }>;
  totalIssues: number;
}) {
  return (
    <div className="flex h-3 overflow-hidden rounded-md bg-muted">
      {data
        .filter((item) => item.value > 0)
        .map((item) => (
          <div
            aria-label={`${item.label}: ${item.value}`}
            className="h-full"
            key={item.label}
            style={{
              backgroundColor: item.color,
              width: `${(item.value / totalIssues) * 100}%`,
            }}
          />
        ))}
    </div>
  );
}

function StatusBars({
  data,
}: {
  data: Array<{ label: string; value: number }>;
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
              {item.label}
            </span>
            <span className="font-semibold">{item.value}</span>
          </div>
          <div className="h-2.5 overflow-hidden rounded bg-muted">
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
  const total = data.reduce((sum, item) => sum + item.value, 0);
  const maxValue = Math.max(...data.map((item) => item.value), 1);

  if (total === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No repositories connected yet.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {data
        .filter((item) => item.value > 0)
        .map((item) => (
          <div className="grid gap-2" key={item.label}>
            <div className="flex items-center justify-between">
              <span className="capitalize text-[15px] text-muted-foreground">
                {item.label}
              </span>
              <span className="font-semibold">
                {item.value}
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {formatPercent(item.value, total)}
                </span>
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded bg-muted">
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

type RiskHotspot = {
  issueCount: number;
  jobId: string;
  path: string;
};

function RiskHotspotsList({ hotspots }: { hotspots: RiskHotspot[] }) {
  if (hotspots.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No risky files available from completed reports.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {hotspots.map((hotspot, index) => (
        <Link
          className="grid gap-2 rounded-md border border-border bg-background p-3 transition-colors hover:bg-muted/35"
          href={`/reviews/${hotspot.jobId}/issues?file_path=${encodeURIComponent(
            hotspot.path,
          )}`}
          key={`${hotspot.jobId}:${hotspot.path}`}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="min-w-0 break-all text-sm font-semibold">
              <span className="mr-2 text-muted-foreground">#{index + 1}</span>
              {hotspot.path}
            </p>
            <span className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-semibold text-muted-foreground">
              <FileWarning aria-hidden="true" className="size-3.5" />
              {hotspot.issueCount}
            </span>
          </div>
        </Link>
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
                <StatusBadge status={job.status} />
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

function buildJobStateData(jobs: ReviewJob[]) {
  return [
    {
      label: "Waiting",
      value: jobs.filter((job) => job.status === "PENDING").length,
    },
    {
      label: "Running",
      value: jobs.filter(
        (job) => ACTIVE_STATUSES.has(job.status) && job.status !== "PENDING",
      ).length,
    },
    {
      label: "Completed",
      value: jobs.filter((job) => job.status === "COMPLETED").length,
    },
    {
      label: "Failed",
      value: jobs.filter((job) => job.status === "FAILED").length,
    },
  ];
}

function buildPlatformData(repositories: Repository[]) {
  return ["github", "gitlab", "unknown"].map((platform) => ({
    label: platform,
    value: repositories.filter(
      (repository) => (repository.platform ?? "unknown") === platform,
    ).length,
  }));
}

function buildRiskHotspots(reports: ReviewReport[]): RiskHotspot[] {
  return reports
    .flatMap((report) =>
      (report.top_risky_files ?? []).map((file) => ({
        issueCount: file.issue_count,
        jobId: report.job_id,
        path: file.path,
      })),
    )
    .sort((leftFile, rightFile) => {
      if (rightFile.issueCount !== leftFile.issueCount) {
        return rightFile.issueCount - leftFile.issueCount;
      }

      return leftFile.path.localeCompare(rightFile.path);
    })
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

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatPercent(value: number, total: number) {
  if (total <= 0) {
    return "0%";
  }

  return `${Math.round((value / total) * 100)}%`;
}

function formatReportReadinessValue(
  loadedReports: number,
  completedReviews: number,
) {
  if (completedReviews === 0) {
    return "No completed reviews";
  }

  return `${loadedReports}/${completedReviews}`;
}

function formatReportReadinessDescription(
  loadedReports: number,
  completedReviews: number,
) {
  if (completedReviews === 0) {
    return "Reports appear here after reviews complete.";
  }

  const percentage = Math.round((loadedReports / completedReviews) * 100);
  return `${percentage}% of completed reviews have reports available.`;
}

function compareReviewJobsByLatest(leftJob: ReviewJob, rightJob: ReviewJob) {
  return getReviewJobTimestamp(rightJob) - getReviewJobTimestamp(leftJob);
}

function getReviewJobTimestamp(job: ReviewJob) {
  return new Date(job.completed_at ?? job.created_at).getTime();
}

function getAverageRunTime(jobs: ReviewJob[]) {
  const durations = jobs
    .filter(
      (job) =>
        job.status === "COMPLETED" &&
        job.started_at !== null &&
        job.completed_at !== null,
    )
    .map((job) =>
      Math.max(
        0,
        Math.round(
          (new Date(job.completed_at ?? "").getTime() -
            new Date(job.started_at ?? "").getTime()) /
            1000,
        ),
      ),
    );

  if (durations.length === 0) {
    return null;
  }

  return Math.round(
    durations.reduce((sum, duration) => sum + duration, 0) / durations.length,
  );
}

function formatDuration(totalSeconds: number | null) {
  if (totalSeconds === null) {
    return "--";
  }

  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }

  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

function getLatestCompletedReviewJob(jobs: ReviewJob[]) {
  return (
    [...jobs]
      .filter((job) => job.status === "COMPLETED")
      .sort(compareReviewJobsByLatest)[0] ?? null
  );
}

function getDashboardSummary({
  activeReviewCount,
  completedReviewCount,
  criticalIssueCount,
  highIssueCount,
  repositoryCount,
  reportCount,
  totalIssueCount,
}: {
  activeReviewCount: number;
  completedReviewCount: number;
  criticalIssueCount: number;
  highIssueCount: number;
  repositoryCount: number;
  reportCount: number;
  totalIssueCount: number;
}): DashboardSummary {
  if (repositoryCount === 0) {
    return {
      description: "No source repository has been added to this workspace yet.",
      icon: GitFork,
      title: "No repositories connected",
    };
  }

  if (completedReviewCount === 0) {
    return {
      description:
        activeReviewCount > 0
          ? `${activeReviewCount} review is running. Results will appear after it completes.`
          : "Repositories are connected, but no review has completed yet.",
      icon: CirclePlay,
      title: activeReviewCount > 0 ? "First review is running" : "No completed reviews yet",
    };
  }

  if (criticalIssueCount > 0) {
    return {
      description: `${criticalIssueCount} critical findings were found in ${reportCount} recent reports.`,
      icon: AlertTriangle,
      title: "Critical findings need attention",
    };
  }

  if (highIssueCount > 0) {
    return {
      description: `${highIssueCount} high severity findings were found in ${reportCount} recent reports.`,
      icon: AlertTriangle,
      title: "High severity findings need review",
    };
  }

  if (totalIssueCount === 0) {
    return {
      description: `${reportCount} recent reports have no findings.`,
      icon: ShieldCheck,
      title: "Latest reviews look clear",
    };
  }

  return {
    description: `${totalIssueCount} findings were found in ${reportCount} recent reports.`,
    icon: AlertTriangle,
    title: "Findings are ready for triage",
  };
}

function getNextStep({
  activeReviewCount,
  completedReviewCount,
  criticalIssueCount,
  highIssueCount,
  latestCompletedJobId,
  repositoryCount,
  totalIssueCount,
}: {
  activeReviewCount: number;
  completedReviewCount: number;
  criticalIssueCount: number;
  highIssueCount: number;
  latestCompletedJobId: string | null;
  repositoryCount: number;
  totalIssueCount: number;
}): NextStep {
  if (repositoryCount === 0) {
    return {
      description: "Add a GitHub or GitLab repository before creating reviews.",
      href: "/repositories",
      label: "Add Repository",
      title: "Connect a repository",
    };
  }

  if (activeReviewCount > 0) {
    return {
      description: "A review is still running. Open the reviews list to watch it.",
      href: "/reviews",
      label: "Open Reviews",
      title: "Check running reviews",
    };
  }

  if (completedReviewCount === 0) {
    return {
      description: "Open a repository and start its first review.",
      href: "/repositories",
      label: "Open Repositories",
      title: "Start the first review",
    };
  }

  if (criticalIssueCount > 0 || highIssueCount > 0 || totalIssueCount > 0) {
    return {
      description: "Open the latest completed review and inspect its findings.",
      href: latestCompletedJobId
        ? `/reviews/${latestCompletedJobId}/issues`
        : "/reviews",
      label: "Review Findings",
      title: "Triage review findings",
    };
  }

  return {
    description: "The latest reports are clear. Open the last report for details.",
    href: latestCompletedJobId ? `/reviews/${latestCompletedJobId}/report` : "/reviews",
    label: "Open Latest Report",
    title: "Read the latest report",
  };
}
