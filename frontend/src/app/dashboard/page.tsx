"use client";

import { AlertCircle, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ActiveReviews } from "@/components/dashboard/active-reviews";
import { DashboardOnboarding } from "@/components/dashboard/onboarding";
import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton";
import { LatestReportSummaryCard } from "@/components/dashboard/latest-report-summary";
import { NeedsAttention } from "@/components/dashboard/needs-attention";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { RepositoryOverview } from "@/components/dashboard/repository-overview";
import { RiskPostureCard } from "@/components/dashboard/risk-posture";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  buildActiveReviews,
  buildLatestReportSummary,
  buildNeedsAttention,
  buildPrimaryAction,
  buildRecentReviews,
  buildRepositoryOverview,
  buildRiskPosture,
  type DashboardData,
} from "@/lib/dashboard";
import { getRepositories } from "@/lib/repositories";
import { getReport } from "@/lib/reports";
import { getReviewJobs } from "@/lib/review-jobs";

const EMPTY_DATA: DashboardData = {
  repositories: [],
  jobs: [],
  reports: [],
};

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData>(EMPTY_DATA);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function loadDashboard() {
    setError(null);
    setIsLoading(true);

    try {
      const [repositories, jobs] = await Promise.all([
        getRepositories(),
        getReviewJobs(),
      ]);

      const completedJobs = jobs
        .filter((job) => job.status === "COMPLETED")
        .slice(0, 12);
      const reportResults = await Promise.allSettled(
        completedJobs.map((job) => getReport(job.id)),
      );
      const reports = reportResults.flatMap((result) =>
        result.status === "fulfilled" ? [result.value] : [],
      );

      setData({ repositories, jobs, reports });
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

  const derived = useMemo(
    () => ({
      posture: buildRiskPosture(data),
      primaryAction: buildPrimaryAction(data),
      needsAttention: buildNeedsAttention(data),
      activeReviews: buildActiveReviews(data),
      recentReviews: buildRecentReviews(data),
      repositoryOverview: buildRepositoryOverview(data),
      latestReport: buildLatestReportSummary(data),
    }),
    [data],
  );

  const hasRepositories = data.repositories.length > 0;
  const showOnboarding = !isLoading && !error && !hasRepositories;

  return (
    <>
      <PageHeader
        actionHref={derived.primaryAction.href}
        actionLabel={derived.primaryAction.label}
        isLoading={isLoading}
        onRefresh={() => void loadDashboard()}
        showPrimaryAction={!showOnboarding}
      />

      {error ? (
        <Card className="border-destructive/40">
          <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertCircle
                aria-hidden="true"
                className="mt-0.5 size-5 shrink-0 text-destructive"
              />
              <div>
                <p className="text-[15px] font-semibold text-foreground">
                  Something went wrong
                </p>
                <p className="mt-1 text-[15px] text-muted-foreground">{error}</p>
              </div>
            </div>
            <Button
              className="shrink-0"
              disabled={isLoading}
              onClick={() => void loadDashboard()}
              variant="secondary"
            >
              <RefreshCw aria-hidden="true" />
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {isLoading ? <DashboardSkeleton /> : null}

      {showOnboarding ? <DashboardOnboarding /> : null}

      {!isLoading && !error && hasRepositories ? (
        <>
          <RiskPostureCard posture={derived.posture} />

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] xl:items-start">
            <div className="grid gap-4">
              <NeedsAttention items={derived.needsAttention} />
              <ActiveReviews reviews={derived.activeReviews} />
              <RecentActivity reviews={derived.recentReviews} />
            </div>

            <div className="grid gap-4">
              {derived.latestReport ? (
                <LatestReportSummaryCard summary={derived.latestReport} />
              ) : null}
              <RepositoryOverview repositories={derived.repositoryOverview} />
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}

function PageHeader({
  actionHref,
  actionLabel,
  isLoading,
  onRefresh,
  showPrimaryAction,
}: {
  actionHref: string;
  actionLabel: string;
  isLoading: boolean;
  onRefresh: () => void;
  showPrimaryAction: boolean;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Security workspace
        </p>
        <h1 className="mt-1 text-3xl font-extrabold tracking-normal">Dashboard</h1>
        <p className="mt-1 text-[15px] leading-6 text-muted-foreground">
          Repository health and review activity across your workspace.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          disabled={isLoading}
          onClick={onRefresh}
          variant="secondary"
        >
          <RefreshCw aria-hidden="true" />
          Refresh
        </Button>
        {showPrimaryAction ? (
          <Button asChild>
            <Link href={actionHref}>{actionLabel}</Link>
          </Button>
        ) : null}
      </div>
    </div>
  );
}
