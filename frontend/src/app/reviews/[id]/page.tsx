"use client";

import {
  ArrowLeft,
  Loader2,
  Trash2,
  Wifi,
  WifiOff,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  shouldPollJobProgress,
  type JobProgressConnectionState,
} from "@/lib/job-progress";
import { useJobProgress } from "@/hooks/use-job-progress";
import {
  deleteReviewJob,
  getReviewJob,
} from "@/lib/review-jobs";
import { ReviewWorkspaceTabs } from "@/components/reviews/review-workspace-tabs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  applyJobProgress,
  setCurrentJob,
  setJobError,
  setJobLoading,
  removeJob,
  setJobMutating,
  upsertJob,
} from "@/store/slices/jobSlice";
import type {
  JobProgressEvent,
  ReviewJob,
  ReviewJobStatus,
} from "@/types/review-job";

const REVIEW_JOB_POLLING_INTERVAL_MS = 5_000;
const TERMINAL_STATUSES = new Set<ReviewJobStatus>(["COMPLETED", "FAILED"]);

export default function ReviewJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const router = useRouter();
  const dispatch = useAppDispatch();
  const { currentJob, error, isLoading, isMutating } = useAppSelector(
    (state) => state.jobs,
  );
  const currentJobStatus = currentJob?.status;
  const [isCanceling, setIsCanceling] = useState(false);
  const [isCancelConfirmationOpen, setIsCancelConfirmationOpen] =
    useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const loadJob = useCallback(async (isBackground = false) => {
    if (!isBackground) {
      dispatch(setJobLoading(true));
    }

    try {
      const job = await getReviewJob(jobId);
      dispatch(setCurrentJob(job));
      dispatch(upsertJob(job));
    } catch (requestError) {
      if (!isBackground) {
        dispatch(
          setJobError(
            getApiErrorMessage(requestError, "Unable to load review job."),
          ),
        );
      }
    } finally {
      if (!isBackground) {
        dispatch(setJobLoading(false));
      }
    }
  }, [dispatch, jobId]);

  const handleProgressEvent = useCallback(
    (event: JobProgressEvent) => {
      dispatch(applyJobProgress(event));
      if (event.event === "completed" || event.event === "failed") {
        void loadJob(true);
      }
    },
    [dispatch, loadJob],
  );

  const { connectionState, progressEvent } = useJobProgress({
    enabled: Boolean(currentJob && !TERMINAL_STATUSES.has(currentJob.status)),
    onEvent: handleProgressEvent,
    streamUrl: currentJob?.stream_url ?? null,
  });

  useEffect(() => {
    void loadJob();

    return () => {
      dispatch(setCurrentJob(null));
    };
  }, [dispatch, loadJob]);

  useEffect(() => {
    if (!currentJobStatus || !shouldPollJobProgress(currentJobStatus)) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadJob(true);
    }, REVIEW_JOB_POLLING_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentJobStatus, loadJob]);

  useEffect(() => {
    const shouldTickDuration =
      currentJob?.started_at !== null && currentJob?.completed_at === null;
    if (!shouldTickDuration) {
      return;
    }

    setNowMs(Date.now());
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1_000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentJob?.completed_at, currentJob?.started_at]);

  async function handleCancelJob() {
    if (!currentJob || TERMINAL_STATUSES.has(currentJob.status)) {
      return;
    }

    setIsCanceling(true);
    dispatch(setJobMutating(true));

    try {
      await deleteReviewJob(currentJob.id);
      dispatch(removeJob(currentJob.id));
      dispatch(setCurrentJob(null));
      setIsCancelConfirmationOpen(false);
      toast.success("Review job canceled.");
      router.push("/reviews");
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to cancel job."));
    } finally {
      setIsCanceling(false);
      dispatch(setJobMutating(false));
    }
  }

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href="/reviews">
            <ArrowLeft aria-hidden="true" />
            Reviews
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2">
          {currentJob && !TERMINAL_STATUSES.has(currentJob.status) ? (
            <Button
              disabled={isMutating || isCanceling}
              onClick={() => setIsCancelConfirmationOpen(true)}
              variant="destructive"
            >
              <Trash2 aria-hidden="true" />
              Cancel
            </Button>
          ) : null}
        </div>
      </div>

      <ReviewWorkspaceTabs activeTab="overview" jobId={jobId} />

      <ConfirmationDialog
        confirmLabel="Cancel review"
        description="This stops the active review and permanently deletes its current data. This action cannot be undone."
        isPending={isCanceling}
        onCancel={() => setIsCancelConfirmationOpen(false)}
        onConfirm={() => void handleCancelJob()}
        open={isCancelConfirmationOpen}
        pendingLabel="Canceling..."
        title="Cancel this review?"
      />

      {isLoading && !currentJob ? <ReviewJobSkeleton /> : null}

      {!isLoading && error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {currentJob ? (
        <ReviewJobDetail
          connectionState={connectionState}
          job={currentJob}
          nowMs={nowMs}
          progressEvent={progressEvent}
        />
      ) : null}
    </>
  );
}

function ReviewJobDetail({
  connectionState,
  job,
  nowMs,
  progressEvent,
}: {
  connectionState: JobProgressConnectionState;
  job: ReviewJob;
  nowMs: number;
  progressEvent: JobProgressEvent | null;
}) {
  return (
    <>
      <section className="grid gap-4 md:grid-cols-3">
        <InfoCard label="Status" value={job.status.replaceAll("_", " ")} />
        <InfoCard
          label="Reviewed commit"
          value={formatCommitSha(job.commit_sha)}
        />
        <InfoCard
          label="Duration"
          value={formatDuration(job.started_at, job.completed_at, nowMs)}
        />
      </section>

      <JobProgressCard
        connectionState={connectionState}
        event={progressEvent}
        job={job}
      />

      <Card>
        <CardHeader>
          <CardTitle>Review Summary</CardTitle>
          <CardDescription>
            User-facing details for this repository review.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <DetailRow
            label="Repository"
            value={job.repository_name ?? "Repository unavailable"}
          />
          <DetailRow label="Branch" value={job.branch ?? "main"} />
          <DetailRow
            label="Reviewed commit"
            value={
              job.commit_sha ? (
                <code className="rounded-md bg-muted px-2 py-1 font-mono text-sm">
                  {formatCommitSha(job.commit_sha)}
                </code>
              ) : (
                "Not available yet"
              )
            }
          />
          <DetailRow label="Started" value={formatOptionalDate(job.started_at)} />
          <DetailRow label="Completed" value={formatOptionalDate(job.completed_at)} />
          <DetailRow
            label="Static analysis"
            value={isStaticAnalysisEnabled(job.options) ? "Enabled" : "Disabled"}
          />
          {job.error_message ? (
            <DetailRow
              label="Failure reason"
              value={<span className="text-destructive">{job.error_message}</span>}
            />
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}

function ProgressBar({ tone, value }: { tone: string; value: number }) {
  return (
    <div className="mt-3 h-2 overflow-hidden rounded-md bg-background">
      <div
        className={`h-full rounded-md transition-[width] duration-500 ease-out ${tone}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

function JobProgressCard({
  connectionState,
  event,
  job,
}: {
  connectionState: JobProgressConnectionState;
  event: JobProgressEvent | null;
  job: ReviewJob;
}) {
  const progress = event?.progress ?? getStatusProgress(job.status);
  const message = event?.message ?? getDefaultProgressMessage(job.status);
  const isTerminal = TERMINAL_STATUSES.has(job.status);
  const isWorking = !isTerminal && connectionState !== "closed";
  const connection = getConnectionState(connectionState);
  const ConnectionIcon = connection.icon;

  return (
    <Card aria-live="polite">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Review progress</CardTitle>
            <CardDescription>Realtime updates for this review job.</CardDescription>
          </div>
          <span
            className={`inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-semibold ${connection.className}`}
          >
            <ConnectionIcon
              aria-hidden="true"
              className={`size-3.5 ${isWorking && (connectionState === "connecting" || connectionState === "reconnecting") ? "animate-spin" : ""}`}
            />
            {connection.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex items-end justify-between gap-3">
          <p className="text-sm text-muted-foreground">{message}</p>
          <p className="text-2xl font-semibold tabular-nums">{progress}%</p>
        </div>
        <ProgressBar
          tone={isTerminal && job.status === "FAILED" ? "bg-destructive" : "bg-sky-400"}
          value={progress}
        />
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>{event?.status ?? job.status.replaceAll("_", " ")}</span>
          <span className={isWorking ? "animate-pulse" : undefined}>
            {isWorking ? "Processing" : isTerminal ? "Finished" : "Waiting"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function getStatusProgress(status: ReviewJobStatus): number {
  const progressByStatus: Record<ReviewJobStatus, number> = {
    PENDING: 0,
    CLONING: 10,
    ANALYZING_STRUCTURE: 35,
    GENERATING_SUMMARY: 50,
    RUNNING_STATIC_ANALYSIS: 60,
    CHUNKING_CODE: 80,
    AI_REVIEWING: 88,
    GENERATING_REPORT: 95,
    COMPLETED: 100,
    FAILED: 100,
  };
  return progressByStatus[status];
}

function getDefaultProgressMessage(status: ReviewJobStatus): string {
  if (status === "COMPLETED") {
    return "Review completed.";
  }
  if (status === "FAILED") {
    return "Review failed.";
  }
  return `Review status: ${status.replaceAll("_", " ").toLowerCase()}`;
}

function getConnectionState(state: JobProgressConnectionState): {
  className: string;
  icon: typeof Wifi;
  label: string;
} {
  if (state === "open") {
    return {
      className: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200",
      icon: Wifi,
      label: "Live",
    };
  }
  if (state === "reconnecting") {
    return {
      className: "border-amber-400/40 bg-amber-400/10 text-amber-800 dark:text-amber-100",
      icon: Loader2,
      label: "Reconnecting",
    };
  }
  if (state === "connecting") {
    return {
      className: "border-sky-400/40 bg-sky-400/10 text-sky-700 dark:text-sky-200",
      icon: Loader2,
      label: "Connecting",
    };
  }
  return {
    className: "border-border bg-muted text-muted-foreground",
    icon: WifiOff,
    label: "Closed",
  };
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-xs font-medium uppercase text-muted-foreground">
          {label}
        </p>
        <p className="mt-2 text-lg font-semibold tracking-normal">{value}</p>
      </CardContent>
    </Card>
  );
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="grid gap-2 border-b border-border pb-4 last:border-b-0 last:pb-0 sm:grid-cols-[180px_1fr]">
      <dt className="text-[15px] text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-[15px] text-foreground">{value}</dd>
    </div>
  );
}

function ReviewJobSkeleton() {
  return (
    <div className="grid gap-4">
      <div className="grid gap-4 md:grid-cols-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <Card key={index}>
            <CardContent className="p-5">
              <div className="h-4 w-20 animate-pulse rounded bg-muted" />
              <div className="mt-3 h-6 w-32 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatOptionalDate(value: string | null) {
  return value ? formatDate(value) : "Not available";
}

function formatCommitSha(value: string | null) {
  return value ? value.slice(0, 7) : "Not available";
}

function formatDuration(
  startedAt: string | null,
  completedAt: string | null,
  nowMs: number,
) {
  if (!startedAt) {
    return "Not started";
  }

  const endedAtMs = completedAt ? new Date(completedAt).getTime() : nowMs;
  const durationSeconds = Math.max(
    0,
    Math.floor((endedAtMs - new Date(startedAt).getTime()) / 1000),
  );

  if (durationSeconds < 60) {
    return `${durationSeconds}s`;
  }

  const minutes = Math.floor(durationSeconds / 60);
  const seconds = durationSeconds % 60;
  if (minutes < 60) {
    return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
  }

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}h ${remainingMinutes}m ${seconds}s`;
}

function isStaticAnalysisEnabled(options: Record<string, unknown> | null) {
  return options?.run_static_analysis !== false;
}
