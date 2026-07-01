"use client";

import { CirclePlay, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import { cancelReviewJob, getReviewJobs } from "@/lib/review-jobs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  removeJob,
  setJobError,
  setJobLoading,
  setJobMutating,
  setJobs,
} from "@/store/slices/jobSlice";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";

const TERMINAL_STATUSES = new Set<ReviewJobStatus>(["COMPLETED", "FAILED"]);

export default function ReviewsPage() {
  const dispatch = useAppDispatch();
  const { error, isLoading, isMutating, items } = useAppSelector(
    (state) => state.jobs,
  );
  const [cancelingJobId, setCancelingJobId] = useState<string | null>(null);

  async function loadJobs() {
    dispatch(setJobLoading(true));

    try {
      dispatch(setJobs(await getReviewJobs()));
    } catch (requestError) {
      dispatch(
        setJobError(getApiErrorMessage(requestError, "Unable to load reviews.")),
      );
    } finally {
      dispatch(setJobLoading(false));
    }
  }

  useEffect(() => {
    void loadJobs();
  }, []);

  async function handleCancelJob(job: ReviewJob) {
    setCancelingJobId(job.id);
    dispatch(setJobMutating(true));

    try {
      await cancelReviewJob(job.id);
      dispatch(removeJob(job.id));
      toast.success("Review job canceled.");
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to cancel job."));
    } finally {
      setCancelingJobId(null);
      dispatch(setJobMutating(false));
    }
  }

  return (
    <>
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Worker queue
          </p>
          <h1 className="mt-2 text-2xl font-extrabold tracking-normal">
            Reviews
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Review jobs created from connected repositories.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/repositories">
              <CirclePlay aria-hidden="true" />
              Start from Repository
            </Link>
          </Button>
        </div>
      </header>

      <Card className="overflow-hidden">
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>Job List</CardTitle>
            <CardDescription>
              Polling detail pages are temporary until SSE lands in Phase 5.
            </CardDescription>
          </div>
          <Button
            disabled={isLoading}
            onClick={() => void loadJobs()}
            variant="secondary"
          >
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? <JobListSkeleton /> : null}
          {!isLoading && error ? (
            <div className="border-t border-border p-6">
              <p className="text-sm text-destructive">{error}</p>
            </div>
          ) : null}
          {!isLoading && !error && items.length === 0 ? <EmptyJobsState /> : null}
          {!isLoading && !error && items.length > 0 ? (
            <JobsTable
              cancelingJobId={cancelingJobId}
              isMutating={isMutating}
              jobs={items}
              onCancelJob={handleCancelJob}
            />
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}

function EmptyJobsState() {
  return (
    <div className="flex flex-col items-center border-t border-border px-6 py-12 text-center">
      <CirclePlay aria-hidden="true" className="size-10 text-muted-foreground" />
      <h2 className="mt-4 text-lg font-semibold tracking-normal">
        No review jobs yet
      </h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Open a repository and start a review to create the first job.
      </p>
      <Button asChild className="mt-5">
        <Link href="/repositories">Open Repositories</Link>
      </Button>
    </div>
  );
}

function JobListSkeleton() {
  return (
    <div className="border-t border-border">
      {Array.from({ length: 4 }).map((_, index) => (
        <div
          className="grid grid-cols-1 gap-3 border-b border-border px-6 py-4 md:grid-cols-[1fr_0.7fr_0.9fr_0.8fr_0.4fr]"
          key={index}
        >
          <div className="h-5 w-44 animate-pulse rounded bg-muted" />
          <div className="h-5 w-24 animate-pulse rounded bg-muted" />
          <div className="h-5 w-36 animate-pulse rounded bg-muted" />
          <div className="h-5 w-36 animate-pulse rounded bg-muted" />
          <div className="h-9 w-10 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

type JobsTableProps = {
  cancelingJobId: string | null;
  isMutating: boolean;
  jobs: ReviewJob[];
  onCancelJob: (job: ReviewJob) => Promise<void>;
};

function JobsTable({
  cancelingJobId,
  isMutating,
  jobs,
  onCancelJob,
}: JobsTableProps) {
  return (
    <div className="overflow-x-auto border-t border-border">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-6 py-3 font-medium">Repository</th>
            <th className="px-6 py-3 font-medium">Branch</th>
            <th className="px-6 py-3 font-medium">Status</th>
            <th className="px-6 py-3 font-medium">Created</th>
            <th className="px-6 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr
              className="border-t border-border transition-colors hover:bg-muted/35"
              key={job.id}
            >
              <td className="px-6 py-4">
                <Link
                  className="font-medium text-foreground hover:text-slate-300"
                  href={`/reviews/${job.id}`}
                >
                  {job.repository_name ?? job.repository_id}
                </Link>
              </td>
              <td className="px-6 py-4 text-muted-foreground">
                {job.branch ?? "main"}
              </td>
              <td className="px-6 py-4">
                <StatusBadge status={job.status} />
              </td>
              <td className="px-6 py-4 text-muted-foreground">
                {formatDate(job.created_at)}
              </td>
              <td className="px-6 py-4 text-right">
                {!TERMINAL_STATUSES.has(job.status) ? (
                  <Button
                    aria-label={`Cancel ${job.repository_name ?? job.id}`}
                    disabled={isMutating || cancelingJobId === job.id}
                    onClick={() => void onCancelJob(job)}
                    size="icon"
                    variant="ghost"
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: ReviewJobStatus }) {
  const className = getStatusClassName(status);

  return (
    <span
      className={`inline-flex rounded-md border px-2 py-1 text-xs font-medium ${className}`}
    >
      {status.replaceAll("_", " ")}
    </span>
  );
}

function getStatusClassName(status: ReviewJobStatus) {
  if (status === "COMPLETED") {
    return "border-slate-500/50 bg-background text-slate-100";
  }

  if (status === "FAILED") {
    return "border-slate-600 bg-background text-slate-300";
  }

  if (status === "AI_REVIEWING") {
    return "border-slate-500/50 bg-slate-900 text-slate-100";
  }

  if (status === "PENDING") {
    return "border-slate-700 bg-background text-muted-foreground";
  }

  return "border-slate-700 bg-background text-muted-foreground";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
