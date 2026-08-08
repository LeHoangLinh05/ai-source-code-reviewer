"use client";

import { AlertCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ActiveReviews } from "@/components/dashboard/active-reviews";
import { DashboardOnboarding } from "@/components/dashboard/onboarding";
import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton";
import { LatestReportSummaryCard } from "@/components/dashboard/latest-report-summary";
import { NeedsAttention } from "@/components/dashboard/needs-attention";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { RepositoryOverview } from "@/components/dashboard/repository-overview";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  buildActiveReviews,
  buildLatestReportSummary,
  buildNeedsAttention,
  buildRecentReviews,
  buildRepositoryOverview,
  selectRecentCompletedJobs,
  type DashboardData,
} from "@/lib/dashboard";
import { getRepositories } from "@/lib/repositories";
import { getReport } from "@/lib/reports";
import { getReviewJobs } from "@/lib/review-jobs";
import type { ReviewReport } from "@/types/report";

const EMPTY_DATA: DashboardData = {
  repositories: [],
  jobs: [],
  reports: [],
  reportLoadFailureJobIds: [],
  reportRequestCount: 0,
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

      const completedJobs = selectRecentCompletedJobs(jobs);
      const reportResults = await Promise.allSettled(
        completedJobs.map((job) => getReport(job.id)),
      );
      const reports: ReviewReport[] = [];
      const reportLoadFailureJobIds: string[] = [];

      for (const [index, result] of reportResults.entries()) {
        if (result.status === "fulfilled") {
          reports.push(result.value);
          continue;
        }

        const completedJob = completedJobs[index];
        if (completedJob) {
          reportLoadFailureJobIds.push(completedJob.id);
        }
      }

      setData({
        repositories,
        jobs,
        reports,
        reportLoadFailureJobIds,
        reportRequestCount: completedJobs.length,
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

  const derived = useMemo(
    () => ({
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
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {isLoading ? <DashboardSkeleton /> : null}

      {showOnboarding ? <DashboardOnboarding /> : null}

      {!isLoading && !error && hasRepositories ? (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] xl:items-start">
          <div className="grid min-w-0 gap-4">
            <NeedsAttention items={derived.needsAttention} />
            <ActiveReviews reviews={derived.activeReviews} />
            <RecentActivity reviews={derived.recentReviews} />
          </div>

          <div className="grid min-w-0 gap-4">
            {derived.latestReport ? (
              <LatestReportSummaryCard summary={derived.latestReport} />
            ) : null}
            <RepositoryOverview repositories={derived.repositoryOverview} />
          </div>
        </div>
      ) : null}
    </>
  );
}
