"use client";

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import { getReviewJob } from "@/lib/review-jobs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  setCurrentJob,
  setJobError,
  setJobLoading,
  upsertJob,
} from "@/store/slices/jobSlice";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";

const POLLING_INTERVAL_MS = 4_000;
const TERMINAL_STATUSES = new Set<ReviewJobStatus>(["COMPLETED", "FAILED"]);

export default function ReviewJobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const dispatch = useAppDispatch();
  const { currentJob, error, isLoading } = useAppSelector((state) => state.jobs);

  async function loadJob() {
    dispatch(setJobLoading(true));

    try {
      const job = await getReviewJob(jobId);
      dispatch(setCurrentJob(job));
      dispatch(upsertJob(job));
    } catch (requestError) {
      dispatch(
        setJobError(getApiErrorMessage(requestError, "Unable to load review job.")),
      );
    } finally {
      dispatch(setJobLoading(false));
    }
  }

  useEffect(() => {
    void loadJob();

    return () => {
      dispatch(setCurrentJob(null));
    };
  }, [jobId]);

  useEffect(() => {
    if (currentJob && TERMINAL_STATUSES.has(currentJob.status)) {
      return;
    }

    // TODO(P5): replace polling with useJobProgress backed by SSE.
    const intervalId = window.setInterval(() => {
      void loadJob();
    }, POLLING_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentJob?.status, jobId]);

  return (
    <>
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <Button asChild className="mb-4" size="sm" variant="ghost">
            <Link href="/reviews">
              <ArrowLeft aria-hidden="true" />
              Reviews
            </Link>
          </Button>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Review execution
          </p>
          <h1 className="mt-2 text-3xl font-extrabold tracking-normal">
            {currentJob?.repository_name ?? "Review Job"}
          </h1>
          <p className="mt-1 text-[15px] leading-6 text-muted-foreground">
            Polling status every 4 seconds until terminal state.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {currentJob?.status === "COMPLETED" ? (
            <>
              <Button asChild variant="secondary">
                <Link href={`/reviews/${jobId}/report`}>Report</Link>
              </Button>
              <Button asChild variant="secondary">
                <Link href={`/reviews/${jobId}/issues`}>Issues</Link>
              </Button>
            </>
          ) : null}
          <Button disabled={isLoading} onClick={() => void loadJob()}>
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </header>

      {isLoading && !currentJob ? <ReviewJobSkeleton /> : null}

      {!isLoading && error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {currentJob ? <ReviewJobDetail job={currentJob} /> : null}
    </>
  );
}

function ReviewJobDetail({ job }: { job: ReviewJob }) {
  return (
    <>
      <section className="grid gap-4 md:grid-cols-3">
        <InfoCard label="Status" value={job.status.replaceAll("_", " ")} />
        <InfoCard label="Branch" value={job.branch ?? "main"} />
        <InfoCard label="Created" value={formatDate(job.created_at)} />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Job Details</CardTitle>
          <CardDescription>
            Worker execution will replace the pending stub in a later slice.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <DetailRow label="Job ID" value={job.id} />
          <DetailRow label="Repository" value={job.repository_name ?? job.repository_id} />
          <DetailRow label="Repository ID" value={job.repository_id} />
          <DetailRow label="Stream URL" value={job.stream_url} />
          <DetailRow label="Commit SHA" value={job.commit_sha ?? "Not available"} />
          <DetailRow label="Started" value={formatOptionalDate(job.started_at)} />
          <DetailRow label="Completed" value={formatOptionalDate(job.completed_at)} />
          <DetailRow
            label="Options"
            value={
              <pre className="overflow-x-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">
                {JSON.stringify(job.options ?? {}, null, 2)}
              </pre>
            }
          />
        </CardContent>
      </Card>
    </>
  );
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
