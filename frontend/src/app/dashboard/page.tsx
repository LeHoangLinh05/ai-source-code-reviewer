"use client";

import { AlertCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ActionCenter } from "@/components/dashboard/action-center";
import { ActivityTimeline } from "@/components/dashboard/activity-timeline";
import { CodebaseHealthCard } from "@/components/dashboard/codebase-health";
import { DashboardOnboarding } from "@/components/dashboard/onboarding";
import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton";
import { QuickStatsRow } from "@/components/dashboard/quick-stats";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  buildActivityTimeline,
  buildCodebaseHealth,
  buildNeedsAttention,
  buildQuickStats,
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
      quickStats: buildQuickStats(data),
      needsAttention: buildNeedsAttention(data),
      codebaseHealth: buildCodebaseHealth(data),
      activityTimeline: buildActivityTimeline(data),
    }),
    [data],
  );

  const hasRepositories = data.repositories.length > 0;
  const showOnboarding = !isLoading && !error && !hasRepositories;

  return (
    <div className="flex flex-col gap-6">
      {error ? (
        <Card className="border-border bg-card">
          <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <AlertCircle
                aria-hidden="true"
                className="mt-0.5 size-5 shrink-0 text-slate-400"
              />
              <div>
                <p className="text-[15px] font-semibold text-foreground">
                  Something went wrong
                </p>
                <p className="mt-1 text-sm text-muted-foreground">{error}</p>
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
        <div className="flex flex-col gap-6">
          {/* Row 1: Quick Stats */}
          <QuickStatsRow stats={derived.quickStats} />

          {/* Row 2: Action Center + Codebase Health (Equal Height) */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-12 items-stretch">
            <div className="flex flex-col min-w-0 lg:col-span-7 xl:col-span-7">
              <ActionCenter items={derived.needsAttention} />
            </div>
            <div className="flex flex-col min-w-0 lg:col-span-5 xl:col-span-5">
              <CodebaseHealthCard health={derived.codebaseHealth} />
            </div>
          </div>

          {/* Row 3: Activity Timeline */}
          <ActivityTimeline groups={derived.activityTimeline} />
        </div>
      ) : null}
    </div>
  );
}