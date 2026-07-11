"use client";

import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Circle,
  FileCode2,
  GitBranch,
  RefreshCw,
  SearchCheck,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
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
import {
  cancelReviewJob,
  getReviewJob,
  getReviewJobAiTrace,
} from "@/lib/review-jobs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  setCurrentJob,
  setJobError,
  setJobLoading,
  removeJob,
  setJobMutating,
  upsertJob,
} from "@/store/slices/jobSlice";
import type {
  AITraceCoverage,
  AITraceStage,
  AITrace,
  AIToolCallTrace,
  ReviewJob,
  ReviewJobStatus,
} from "@/types/review-job";

const POLLING_INTERVAL_MS = 4_000;
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
  const [aiTrace, setAiTrace] = useState<AITrace | null>(null);
  const [aiTraceError, setAiTraceError] = useState<string | null>(null);
  const [isCanceling, setIsCanceling] = useState(false);

  const loadJob = useCallback(async () => {
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
  }, [dispatch, jobId]);

  const loadAiTrace = useCallback(async () => {
    try {
      setAiTrace(await getReviewJobAiTrace(jobId));
      setAiTraceError(null);
    } catch (requestError) {
      setAiTraceError(
        getApiErrorMessage(requestError, "Unable to load AI trace."),
      );
    }
  }, [jobId]);

  useEffect(() => {
    void loadJob();
    void loadAiTrace();

    return () => {
      dispatch(setCurrentJob(null));
    };
  }, [dispatch, loadAiTrace, loadJob]);

  useEffect(() => {
    if (currentJobStatus && TERMINAL_STATUSES.has(currentJobStatus)) {
      return;
    }

    // TODO(P5): replace polling with useJobProgress backed by SSE.
    const intervalId = window.setInterval(() => {
      void loadJob();
      void loadAiTrace();
    }, POLLING_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentJobStatus, loadAiTrace, loadJob]);

  async function handleCancelJob() {
    if (!currentJob || TERMINAL_STATUSES.has(currentJob.status)) {
      return;
    }

    setIsCanceling(true);
    dispatch(setJobMutating(true));

    try {
      await cancelReviewJob(currentJob.id);
      dispatch(removeJob(currentJob.id));
      dispatch(setCurrentJob(null));
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
          {currentJob && !TERMINAL_STATUSES.has(currentJob.status) ? (
            <Button
              disabled={isMutating || isCanceling}
              onClick={() => void handleCancelJob()}
              variant="destructive"
            >
              <Trash2 aria-hidden="true" />
              Cancel
            </Button>
          ) : null}
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
          <Button
            disabled={isLoading}
            onClick={() => {
              void loadJob();
              void loadAiTrace();
            }}
          >
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

      {currentJob ? (
        <ReviewJobDetail
          aiTrace={aiTrace}
          aiTraceError={aiTraceError}
          job={currentJob}
        />
      ) : null}
    </>
  );
}

function ReviewJobDetail({
  aiTrace,
  aiTraceError,
  job,
}: {
  aiTrace: AITrace | null;
  aiTraceError: string | null;
  job: ReviewJob;
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
          value={formatDuration(job.started_at, job.completed_at)}
        />
      </section>

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

      <AITracePanel error={aiTraceError} trace={aiTrace} />
    </>
  );
}

function AITracePanel({
  error,
  trace,
}: {
  error: string | null;
  trace: AITrace | null;
}) {
  const latestStatus = trace?.latest_tool_status ?? "waiting";
  const isIncompleteAiReport =
    trace?.report_model === "langchain-react-agent-v1" &&
    !trace.coverage.generated_report_by_ai;
  const latestStatusTone = getToolStatusTone(latestStatus);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>AI Live Trace</CardTitle>
            <CardDescription>
              Pipeline coverage, agent tool calls, generated issues, and report handoff.
            </CardDescription>
          </div>
          <span
            className={[
              "inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-semibold",
              latestStatusTone.container,
            ].join(" ")}
          >
            {latestStatus === "error" ? (
              <AlertTriangle aria-hidden="true" />
            ) : latestStatus === "rejected" ? (
              <AlertTriangle aria-hidden="true" />
            ) : trace?.has_ai_started ? (
              <CheckCircle2 aria-hidden="true" />
            ) : (
              <Activity aria-hidden="true" />
            )}
            {trace?.has_ai_started ? latestStatus : "waiting"}
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid gap-5">
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {isIncompleteAiReport ? (
          <div className="rounded-md border border-amber-400/30 bg-amber-400/10 p-4 text-[15px] leading-6 text-amber-100">
            The AI report exists, but the agent has not read every target chunk yet:
            {" "}
            {trace.coverage.ai_read_target_chunks}/{trace.coverage.target_chunks} target
            chunks.
            This run should be treated as incomplete.
          </div>
        ) : null}

        <section className="grid gap-3 md:grid-cols-3">
          <TraceMetric label="Tool calls" value={trace?.tool_call_count ?? 0} />
          <TraceMetric label="AI issues" value={trace?.ai_issue_count ?? 0} />
          <TraceMetric label="Static issues" value={trace?.static_issue_count ?? 0} />
        </section>

        {trace ? (
          <>
            <StageTimeline stages={trace.stages} />
            <CoverageSummary coverage={trace.coverage} />
          </>
        ) : null}

        {trace?.recent_tool_calls.length ? (
          <ol className="grid gap-3">
            {trace.recent_tool_calls.map((toolCall, index) => (
              <ToolCallRow
                key={`${toolCall.sequence}-${toolCall.tool_name}-${toolCall.called_at}-${index}`}
                call={toolCall}
              />
            ))}
          </ol>
        ) : (
          <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-[15px] text-muted-foreground">
            Waiting for the AI agent to call its first tool.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function StageTimeline({ stages }: { stages: AITraceStage[] }) {
  return (
    <section className="grid gap-3">
      <div className="flex items-center gap-2">
        <Activity aria-hidden="true" className="size-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold uppercase text-muted-foreground">
          Execution timeline
        </h2>
      </div>
      <ol className="grid gap-3 lg:grid-cols-7">
        {stages.map((stage) => (
          <StageItem key={stage.key} stage={stage} />
        ))}
      </ol>
    </section>
  );
}

function StageItem({ stage }: { stage: AITraceStage }) {
  const Icon = getStageIcon(stage);
  const statusClass = getStageStatusClass(stage.status);

  return (
    <li className="min-w-0 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className={`inline-flex size-8 items-center justify-center rounded-md ${statusClass.icon}`}>
          <Icon aria-hidden="true" className="size-4" />
        </span>
        <span className={`rounded-md px-2 py-1 text-[11px] font-semibold uppercase ${statusClass.badge}`}>
          {stage.status}
        </span>
      </div>
      <p className="mt-3 text-sm font-semibold text-foreground">{stage.label}</p>
      <p className="mt-1 min-h-10 text-xs leading-5 text-muted-foreground">
        {stage.detail}
      </p>
      <ProgressBar value={stage.progress_percent} tone={statusClass.bar} />
      {stage.total > 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {stage.current}/{stage.total}
        </p>
      ) : null}
    </li>
  );
}

function CoverageSummary({ coverage }: { coverage: AITraceCoverage }) {
  return (
    <section className="grid gap-3 rounded-md border border-border bg-muted/20 p-4">
      <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
        <p className="text-xs font-semibold uppercase text-muted-foreground">
          AI review mode
        </p>
        <p className="text-sm font-semibold text-foreground">
          {coverage.review_mode.replace("_", " ")}
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <CoverageBar
          label="Files selected by pipeline"
          value={coverage.total_reviewable_files}
          detail={`${coverage.total_reviewable_lines} lines`}
          percent={coverage.total_reviewable_files > 0 ? 100 : 0}
        />
        <CoverageBar
          label="Target chunks read"
          value={coverage.ai_read_target_chunks}
          detail={`${coverage.target_chunks} target / ${coverage.total_chunks} available`}
          percent={coverage.ai_read_chunk_percent}
        />
        <CoverageBar
          label="Target files touched"
          value={coverage.ai_read_files}
          detail={`${coverage.target_files} target files`}
          percent={coverage.ai_read_file_percent}
        />
      </div>
    </section>
  );
}

function CoverageBar({
  detail,
  label,
  percent,
  value,
}: {
  detail: string;
  label: string;
  percent: number;
  value: number;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
        <p className="text-sm font-semibold text-foreground">{value}</p>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
      <ProgressBar value={percent} tone="bg-sky-400" />
    </div>
  );
}

function ProgressBar({ tone, value }: { tone: string; value: number }) {
  return (
    <div className="mt-3 h-2 overflow-hidden rounded-md bg-background">
      <div
        className={`h-full rounded-md ${tone}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

function TraceMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-muted/40 p-4">
      <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-normal">{value}</p>
    </div>
  );
}

function getStageIcon(stage: AITraceStage) {
  if (stage.key === "clone") {
    return GitBranch;
  }
  if (stage.key === "structure" || stage.key === "chunks") {
    return FileCode2;
  }
  if (stage.key === "static") {
    return SearchCheck;
  }
  if (stage.key === "ai") {
    return Bot;
  }
  if (stage.status === "completed") {
    return CheckCircle2;
  }
  return Circle;
}

function getStageStatusClass(status: string) {
  if (status === "completed") {
    return {
      badge: "bg-emerald-500/10 text-emerald-300",
      bar: "bg-emerald-400",
      icon: "bg-emerald-500/10 text-emerald-300",
    };
  }
  if (status === "running") {
    return {
      badge: "bg-sky-500/10 text-sky-200",
      bar: "bg-sky-400",
      icon: "bg-sky-500/10 text-sky-200",
    };
  }
  if (status === "warning") {
    return {
      badge: "bg-amber-400/10 text-amber-100",
      bar: "bg-amber-300",
      icon: "bg-amber-400/10 text-amber-100",
    };
  }
  if (status === "failed") {
    return {
      badge: "bg-destructive/10 text-destructive",
      bar: "bg-destructive",
      icon: "bg-destructive/10 text-destructive",
    };
  }
  return {
    badge: "bg-muted text-muted-foreground",
    bar: "bg-muted-foreground",
    icon: "bg-background text-muted-foreground",
  };
}

function ToolCallRow({ call }: { call: AIToolCallTrace }) {
  const statusTone = getToolStatusTone(call.status);

  return (
    <li className="rounded-md border border-border p-4">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">
            #{call.sequence} {call.tool_name}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatDate(call.called_at)} · {call.duration_ms}ms
          </p>
        </div>
        <span
          className={[
            "inline-flex h-8 items-center rounded-md px-2 text-xs font-semibold",
            statusTone.badge,
          ].join(" ")}
        >
          {call.status}
        </span>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <TraceJsonBlock label="Input" value={call.input} />
        <TraceJsonBlock label="Output" value={call.output} />
      </div>
    </li>
  );
}

function getToolStatusTone(status: string) {
  if (status === "error") {
    return {
      badge: "bg-destructive/10 text-destructive",
      container: "border-destructive/40 bg-destructive/10 text-destructive",
    };
  }
  if (status === "rejected" || status === "warning") {
    return {
      badge: "bg-amber-400/10 text-amber-100",
      container: "border-amber-400/30 bg-amber-400/10 text-amber-100",
    };
  }
  if (status === "ok" || status === "created") {
    return {
      badge: "bg-emerald-500/10 text-emerald-300",
      container: "border-border bg-muted text-muted-foreground",
    };
  }

  return {
    badge: "bg-muted text-muted-foreground",
    container: "border-border bg-muted text-muted-foreground",
  };
}

function TraceJsonBlock({
  label,
  value,
}: {
  label: string;
  value: Record<string, unknown>;
}) {
  return (
    <div className="min-w-0 rounded-md bg-background p-3">
      <p className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
        {label}
      </p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
        {formatTraceJson(value)}
      </pre>
    </div>
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

function formatCommitSha(value: string | null) {
  return value ? value.slice(0, 7) : "Not available";
}

function formatDuration(startedAt: string | null, completedAt: string | null) {
  if (!startedAt) {
    return "Not started";
  }

  if (!completedAt) {
    return "In progress";
  }

  const durationSeconds = Math.max(
    0,
    Math.round((new Date(completedAt).getTime() - new Date(startedAt).getTime()) / 1000),
  );

  if (durationSeconds < 60) {
    return `${durationSeconds}s`;
  }

  const minutes = Math.floor(durationSeconds / 60);
  const seconds = durationSeconds % 60;
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

function isStaticAnalysisEnabled(options: Record<string, unknown> | null) {
  return options?.run_static_analysis !== false;
}

function formatTraceJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2);
}
