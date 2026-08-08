"use client";

import {
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  GitPullRequest,
  Loader2,
  RotateCcw,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ReviewWorkspaceTabs } from "@/components/reviews/review-workspace-tabs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TechnicalDetails } from "@/components/ui/technical-details";
import { useFixJobProgress } from "@/hooks/use-fix-job-progress";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  ACTIVE_FIX_STATUSES,
  canCancelPublish,
  canPublishFix,
  canPublishViaFork,
  canRetryPublish,
  canShowFixDiff,
  formatFixPublishStatus,
  formatFixStatus,
  getFixFailureReason,
  getFixProgressMessage,
  getFixProgressPercent,
  isFixLive,
  splitUnifiedDiffByFile,
} from "@/lib/fix-job-view";
import {
  type FixJobProgressConnectionState,
  shouldReconcileFixJobProgress,
} from "@/lib/fix-job-progress";
import {
  cancelFixPublish,
  getFixDiff,
  listFixJobs,
  publishFixJob,
  retryFixPublish,
} from "@/lib/fix-jobs";
import {
  buildGitHubInstallUrl,
  getGitHubInstallUrl,
  getRepositoryProviderStatus,
} from "@/lib/providers";
import { getReviewJob } from "@/lib/review-jobs";
import type {
  FixDiff,
  FixJob,
  FixJobProgressEvent,
  FixJobStatus,
  FixValidationCheck,
  FixValidationCheckStatus,
  FixValidationResult,
  FixValidationStatus,
} from "@/types/fix-job";
import type { RepositoryProviderStatus } from "@/types/provider";
import type { ReviewJob } from "@/types/review-job";

const FIX_RECONCILIATION_INTERVAL_MS = 10_000;

export default function ReviewFixesPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const [diff, setDiff] = useState<FixDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixes, setFixes] = useState<FixJob[]>([]);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [providerStatus, setProviderStatus] =
    useState<RepositoryProviderStatus | null>(null);
  const [reviewJob, setReviewJob] = useState<ReviewJob | null>(null);
  const [pendingAction, setPendingAction] = useState<
    "publish" | "fork" | "retry" | "cancel" | "connect" | null
  >(null);
  const [selectedFixId, setSelectedFixId] = useState<string | null>(null);

  const selectedFix = useMemo(
    () => fixes.find((fix) => fix.id === selectedFixId) ?? fixes[0] ?? null,
    [fixes, selectedFixId],
  );

  const loadFixes = useCallback(async (isBackground = false) => {
    if (!isBackground) {
      setIsLoading(true);
      setError(null);
    }

    try {
      const fixJobs = await listFixJobs(jobId);
      setFixes(fixJobs);
      setSelectedFixId((currentFixId) => currentFixId ?? fixJobs[0]?.id ?? null);
    } catch (requestError) {
      if (!isBackground) {
        setError(getApiErrorMessage(requestError, "Unable to load fixes."));
      }
    } finally {
      if (!isBackground) {
        setIsLoading(false);
      }
    }
  }, [jobId]);

  const loadReviewJob = useCallback(async () => {
    try {
      setReviewJob(await getReviewJob(jobId));
    } catch {
      setReviewJob(null);
    }
  }, [jobId]);

  const loadProviderStatus = useCallback(async () => {
    if (!reviewJob?.repository_id) {
      setProviderStatus(null);
      return;
    }

    try {
      setProviderStatus(
        await getRepositoryProviderStatus(reviewJob.repository_id),
      );
    } catch {
      setProviderStatus(null);
    }
  }, [reviewJob?.repository_id]);

  const loadDiff = useCallback(async (fixId: string) => {
    setIsDiffLoading(true);

    try {
      setDiff(await getFixDiff(fixId));
    } catch (requestError) {
      setDiff(null);
      setError(getApiErrorMessage(requestError, "Fix diff is not ready yet."));
    } finally {
      setIsDiffLoading(false);
    }
  }, []);

  const handleProgressEvent = useCallback(
    (event: FixJobProgressEvent) => {
      const publishStatus = event.data.publish_status;
      const publishError = event.data.publish_error;
      setFixes((currentFixes) =>
        currentFixes.map((fix) =>
          fix.id === event.fix_id
            ? {
                ...fix,
                status: event.status,
                ...(typeof publishStatus === "string"
                  ? {
                      publish_status:
                        publishStatus as FixJob["publish_status"],
                    }
                  : {}),
                ...(typeof publishError === "string" || publishError === null
                  ? { publish_error: publishError }
                  : {}),
              }
            : fix,
        ),
      );
      void loadFixes(true);
    },
    [loadFixes],
  );

  const selectedFixStreamUrl =
    selectedFix && isFixLive(selectedFix)
      ? selectedFix.stream_url
      : null;
  const { connectionState, progressEvent } = useFixJobProgress({
    enabled: Boolean(selectedFixStreamUrl),
    onEvent: handleProgressEvent,
    streamUrl: selectedFixStreamUrl,
  });

  useEffect(() => {
    void loadFixes();
  }, [loadFixes]);

  useEffect(() => {
    void loadReviewJob();
  }, [loadReviewJob]);

  useEffect(() => {
    void loadProviderStatus();
  }, [loadProviderStatus]);

  useEffect(() => {
    if (!selectedFix || !canShowFixDiff(selectedFix)) {
      setDiff(null);
      return;
    }

    void loadDiff(selectedFix.id);
  }, [loadDiff, selectedFix]);

  useEffect(() => {
    if (
      !selectedFix ||
      !shouldReconcileFixJobProgress(
        connectionState,
        selectedFix.status,
        selectedFix.publish_status,
      )
    ) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadFixes(true);
    }, FIX_RECONCILIATION_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [connectionState, loadFixes, selectedFix]);

  async function publishSelectedFix() {
    if (!selectedFix || !canPublishFix(selectedFix)) {
      return;
    }

    const allowFailedValidation = shouldAllowFailedValidation(selectedFix);
    if (allowFailedValidation === null) {
      return;
    }

    setPendingAction("fork");

    try {
      const updatedFix = await publishFixJob(selectedFix.id, {
        allow_failed_validation: allowFailedValidation,
        strategy: "fork",
      });
      setFixes((currentFixes) =>
        currentFixes.map((fix) => (fix.id === updatedFix.id ? updatedFix : fix)),
      );
      toast.success("Pull request publishing queued.");
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to publish PR."));
    } finally {
      setPendingAction(null);
    }
  }

  async function retrySelectedPublish() {
    if (!selectedFix || !canRetryPublish(selectedFix)) {
      return;
    }

    setPendingAction("retry");
    try {
      const updatedFix = await retryFixPublish(selectedFix.id);
      setFixes((currentFixes) =>
        currentFixes.map((fix) => (fix.id === updatedFix.id ? updatedFix : fix)),
      );
      toast.success("Publish retry queued.");
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to retry publish."));
    } finally {
      setPendingAction(null);
    }
  }

  async function cancelSelectedPublish() {
    if (!selectedFix || !canCancelPublish(selectedFix)) {
      return;
    }

    setPendingAction("cancel");
    try {
      const updatedFix = await cancelFixPublish(selectedFix.id);
      setFixes((currentFixes) =>
        currentFixes.map((fix) => (fix.id === updatedFix.id ? updatedFix : fix)),
      );
      toast.success("Publish canceled.");
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to cancel publish."));
    } finally {
      setPendingAction(null);
    }
  }

  async function connectGitHubApp() {
    setPendingAction("connect");
    try {
      const response = await getGitHubInstallUrl();
      window.location.assign(
        buildGitHubInstallUrl(
          response.install_url,
          `/reviews/${jobId}/fixes`,
        ),
      );
    } catch (requestError) {
      toast.error(
        getApiErrorMessage(requestError, "Unable to open GitHub App install URL."),
      );
      setPendingAction(null);
    }
  }

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href={`/reviews/${jobId}`}>
            <ArrowLeft aria-hidden="true" />
            Review Job
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2">
          {selectedFix && canPublishViaFork(selectedFix) ? (
            <Button
              disabled={pendingAction !== null}
              onClick={() => void publishSelectedFix()}
            >
              <GitPullRequest aria-hidden="true" />
              Publish via fork
            </Button>
          ) : null}
          {selectedFix && canRetryPublish(selectedFix) ? (
            <Button
              disabled={pendingAction !== null}
              onClick={() => void retrySelectedPublish()}
              variant="secondary"
            >
              <RotateCcw aria-hidden="true" />
              Retry publish
            </Button>
          ) : null}
          {selectedFix && canCancelPublish(selectedFix) ? (
            <Button
              disabled={pendingAction !== null}
              onClick={() => void cancelSelectedPublish()}
              variant="destructive"
            >
              <XCircle aria-hidden="true" />
              Cancel publish
            </Button>
          ) : null}
        </div>
      </div>

      <ReviewWorkspaceTabs activeTab="fixes" jobId={jobId} />

      {!isLoading && error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {isLoading && fixes.length === 0 ? <FixesSkeleton /> : null}

      {!isLoading && fixes.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No Fixes Yet</CardTitle>
          </CardHeader>
        </Card>
      ) : null}

      {fixes.length > 0 ? (
        <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card xl:h-[calc(100dvh-12rem)]">
          <div className="grid min-h-0 flex-1 xl:grid-cols-[340px_minmax(0,1fr)]">
            <aside className="flex min-h-0 flex-col border-b border-border xl:border-b-0 xl:border-r">
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <div className="grid content-start gap-3">
                  {fixes.map((fix) => (
                    <button
                      className={`rounded-md border p-4 text-left transition-colors ${
                        selectedFix?.id === fix.id
                          ? "border-foreground bg-muted/50"
                          : "border-border bg-card hover:bg-muted/35"
                      }`}
                      key={fix.id}
                      onClick={() => setSelectedFixId(fix.id)}
                      type="button"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <p className="break-all text-sm font-semibold">
                          {fix.fix_branch}
                        </p>
                        <StatusPill status={fix.status} />
                      </div>
                      {fix.publish_status !== "NOT_REQUESTED" ? (
                        <p className="mt-2 text-xs font-medium text-muted-foreground">
                          Publish: {formatFixPublishStatus(fix.publish_status)}
                        </p>
                      ) : null}
                      <p className="mt-3 text-xs text-muted-foreground">
                        {fix.issue_ids.length} issue
                        {fix.issue_ids.length === 1 ? "" : "s"} ·{" "}
                        {formatTimestamp(fix.created_at)}
                      </p>
                      {fix.changed_files?.length ? (
                        <p className="mt-2 text-xs text-muted-foreground">
                          {fix.changed_files.length} changed file
                          {fix.changed_files.length === 1 ? "" : "s"}
                        </p>
                      ) : null}
                    </button>
                  ))}
                </div>
              </div>
            </aside>

            <div className="min-h-0 overflow-y-auto">
              {selectedFix ? (
                <div className="p-4 xl:p-5">
                  <FixDetails
                    connectionState={connectionState}
                    diff={diff}
                    fix={selectedFix}
                    isConnectPending={pendingAction === "connect"}
                    isDiffLoading={isDiffLoading}
                    jobId={jobId}
                    providerStatus={providerStatus}
                    onConnectGithub={() => void connectGitHubApp()}
                    progressEvent={progressEvent}
                  />
                </div>
              ) : (
                <div className="flex h-full min-h-[24rem] items-center justify-center p-6 text-sm text-muted-foreground">
                  Select a fix to inspect its progress and publish details.
                </div>
              )}
            </div>
          </div>
        </section>
      ) : null}
    </>
  );
}

function FixDetails({
  connectionState,
  diff,
  fix,
  isConnectPending,
  isDiffLoading,
  jobId,
  providerStatus,
  onConnectGithub,
  progressEvent,
}: {
  connectionState: FixJobProgressConnectionState;
  diff: FixDiff | null;
  fix: FixJob;
  isConnectPending: boolean;
  isDiffLoading: boolean;
  jobId: string;
  providerStatus: RepositoryProviderStatus | null;
  onConnectGithub: () => void;
  progressEvent: FixJobProgressEvent | null;
}) {
  const diffSections = diff ? splitUnifiedDiffByFile(diff) : [];
  const failureReason =
    fix.status === "FAILED" ? getFixFailureReason(fix) : null;

  return (
    <div className="grid min-w-0 gap-4">
      <FixProgressCard
        connectionState={connectionState}
        event={progressEvent}
        fix={fix}
      />

      <PublishStatusCard
        fix={fix}
        isConnectPending={isConnectPending}
        jobId={jobId}
        providerStatus={providerStatus}
        onConnectGithub={onConnectGithub}
      />

      <Card>
        <CardHeader>
          <CardTitle>Patch Preview</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-3 rounded-md border border-border bg-background p-4 sm:grid-cols-3">
            <Metric label="Status" value={formatFixStatus(fix.status)} />
            <Metric
              label="Validation"
              value={formatValidationStatus(fix.validation_status)}
            />
            <Metric label="Changed files" value={fix.changed_files?.length ?? 0} />
          </div>

          {failureReason ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <AlertTriangle aria-hidden="true" className="mr-2 inline size-4" />
              {failureReason}
            </p>
          ) : null}

          {fix.changed_files?.length ? (
            <div className="grid gap-2">
              <p className="text-sm font-semibold">Changed Files</p>
              <div className="flex flex-wrap gap-2">
                {fix.changed_files.map((filePath) => (
                  <span
                    className="rounded-md border border-border bg-muted px-2 py-1 font-mono text-xs"
                    key={filePath}
                  >
                    {filePath}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {isDiffLoading ? (
            <div className="h-40 animate-pulse rounded-md bg-muted" />
          ) : null}

          {!isDiffLoading && diff ? (
            <div className="grid gap-3">
              {diffSections.map((section) => (
                <div
                  className="overflow-hidden rounded-md border border-border bg-background"
                  key={section.filePath}
                >
                  <div className="border-b border-border bg-muted/60 px-3 py-2">
                    <p className="break-all font-mono text-xs font-semibold">
                      {section.filePath}
                    </p>
                  </div>
                  <pre className="max-h-[420px] overflow-auto p-4 text-xs leading-5">
                    <code>{section.diff}</code>
                  </pre>
                </div>
              ))}
            </div>
          ) : null}

          {!isDiffLoading && !diff && ACTIVE_FIX_STATUSES.has(fix.status) ? (
            <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">
              Patch generation is still running.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <TechnicalDetails title="Validation details">
        <ValidationResults result={fix.validation_summary} />
      </TechnicalDetails>
    </div>
  );
}

function PublishStatusCard({
  fix,
  isConnectPending,
  jobId,
  providerStatus,
  onConnectGithub,
}: {
  fix: FixJob;
  isConnectPending: boolean;
  jobId: string;
  providerStatus: RepositoryProviderStatus | null;
  onConnectGithub: () => void;
}) {
  const publishError = fix.publish_error;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Publish</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 rounded-md border border-border bg-background p-4 sm:grid-cols-3">
          <Metric
            label="Publish status"
            value={formatFixPublishStatus(fix.publish_status)}
          />
          <Metric
            label="Published branch"
            value={fix.published_branch ?? fix.fix_branch}
          />
          <Metric
            label="Provider"
            value={formatProviderValue(fix, providerStatus)}
          />
        </div>

        {fix.pr_url ? (
          <Button asChild className="w-fit" variant="secondary">
            <a href={fix.pr_url} rel="noreferrer" target="_blank">
              <ExternalLink aria-hidden="true" />
              Open Pull Request
            </a>
          </Button>
        ) : null}

        {fix.publish_status === "NEEDS_FORK" ? (
          <p className="rounded-md border border-amber-400/40 bg-amber-400/10 p-4 text-sm text-amber-900 dark:text-amber-100">
            This fix will be published from your fork namespace.
          </p>
        ) : null}

        {fix.publish_status === "STALE_BASE" ? (
          <div className="grid gap-3 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <p>
              Base branch changed after this review. Regenerate the fix so the
              patch is based on the latest branch head.
            </p>
            <Button asChild className="w-fit" size="sm" variant="secondary">
              <Link href={`/reviews/${jobId}/issues`}>Regenerate fix</Link>
            </Button>
          </div>
        ) : null}

        {publishError ? (
          <p className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <AlertTriangle aria-hidden="true" className="mr-2 inline size-4" />
            {publishError}
          </p>
        ) : null}

        {shouldShowGitHubConnect(fix, providerStatus) ? (
          <Button
            className="w-fit"
            disabled={isConnectPending}
            onClick={onConnectGithub}
            variant="secondary"
          >
            {isConnectPending ? (
              <Loader2 aria-hidden="true" className="animate-spin" />
            ) : (
              <GitPullRequest aria-hidden="true" />
            )}
            Connect GitHub App
          </Button>
        ) : null}

      </CardContent>
    </Card>
  );
}

function ValidationResults({ result }: { result: FixValidationResult | null }) {
  if (!result) {
    return (
      <p className="text-sm text-muted-foreground">
        Validation has not produced a result yet.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      <div className="rounded-md border border-border bg-background p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold">{result.summary}</p>
          <ValidationSummaryPill status={result.status} />
        </div>
      </div>

      {result.checks.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No validation commands ran for this patch.
        </p>
      ) : null}

      {result.checks.map((check) => (
        <ValidationCheckCard check={check} key={`${check.name}:${check.command}`} />
      ))}
    </div>
  );
}

function ValidationCheckCard({ check }: { check: FixValidationCheck }) {
  const tone = getValidationCheckTone(check.status);

  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-xs font-semibold">{check.name}</p>
        <span className={`rounded-md border px-2 py-1 text-xs ${tone}`}>
          {check.status}
        </span>
      </div>
      <p className="mt-2 break-all font-mono text-xs text-muted-foreground">
        {check.command}
      </p>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        <span>Exit: {check.exit_code ?? "n/a"}</span>
        <span>{check.duration_ms}ms</span>
      </div>
      {check.stdout ? (
        <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs">
          {check.stdout}
        </pre>
      ) : null}
      {check.stderr ? (
        <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs">
          {check.stderr}
        </pre>
      ) : null}
    </div>
  );
}

function FixProgressCard({
  connectionState,
  event,
  fix,
}: {
  connectionState: FixJobProgressConnectionState;
  event: FixJobProgressEvent | null;
  fix: FixJob;
}) {
  const progress =
    event?.fix_id === fix.id ? event.progress : getFixProgressPercent(fix.status);
  const message =
    event?.fix_id === fix.id ? event.message : getFixProgressMessage(fix);
  const connection = getConnectionState(connectionState);
  const ConnectionIcon = connection.icon;
  const isActive = isFixLive(fix);
  const isReconnecting =
    connectionState === "connecting" || connectionState === "reconnecting";

  return (
    <Card aria-live="polite">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Fix progress</CardTitle>
          </div>
          <span
            className={`inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-semibold ${connection.className}`}
          >
            <ConnectionIcon
              aria-hidden="true"
              className={`size-3.5 ${isActive && isReconnecting ? "animate-spin" : ""}`}
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
          tone={fix.status === "FAILED" ? "bg-destructive" : "bg-sky-400"}
          value={progress}
        />
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>{formatFixStatus(fix.status)}</span>
          <span className={isActive ? "animate-pulse" : undefined}>
            {isActive ? "Processing" : "Finished"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function ProgressBar({ tone, value }: { tone: string; value: number }) {
  return (
    <div className="h-2 overflow-hidden rounded-md bg-background">
      <div
        className={`h-full rounded-md transition-[width] duration-500 ease-out ${tone}`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

function ValidationSummaryPill({ status }: { status: FixValidationStatus }) {
  return (
    <span
      className={`rounded-md border px-2 py-1 text-xs font-semibold ${getValidationSummaryTone(status)}`}
    >
      {formatValidationStatus(status)}
    </span>
  );
}

function StatusPill({ status }: { status: FixJobStatus }) {
  return (
    <span className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-semibold">
      {formatFixStatus(status)}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 break-words text-sm font-semibold text-foreground">
        {value}
      </p>
    </div>
  );
}

function FixesSkeleton() {
  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card xl:h-[calc(100dvh-12rem)]">
      <div className="h-14 border-b border-border bg-muted/30" />
      <div className="grid min-h-0 flex-1 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="border-b border-border xl:border-b-0 xl:border-r">
          <div className="grid gap-3 p-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div
                className="h-28 animate-pulse rounded-md bg-muted"
                key={index}
              />
            ))}
          </div>
        </div>
        <div className="grid gap-4 p-4 xl:p-5">
          <div className="h-48 animate-pulse rounded-md bg-muted" />
          <div className="h-64 animate-pulse rounded-md bg-muted" />
          <div className="h-96 animate-pulse rounded-md bg-muted" />
        </div>
      </div>
    </div>
  );
}

function getConnectionState(state: FixJobProgressConnectionState): {
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

function getValidationSummaryTone(status: FixValidationStatus) {
  if (status === "PASSED") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  }
  if (status === "FAILED") {
    return "border-destructive/40 bg-destructive/10 text-destructive";
  }

  return "border-border bg-muted text-muted-foreground";
}

function getValidationCheckTone(status: FixValidationCheckStatus) {
  if (status === "passed") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  }
  if (status === "failed") {
    return "border-destructive/40 bg-destructive/10 text-destructive";
  }

  return "border-border bg-muted text-muted-foreground";
}

function shouldAllowFailedValidation(fix: FixJob): boolean | null {
  if (fix.validation_status !== "FAILED") {
    return false;
  }

  return window.confirm(
    "Validation failed for this patch. Publish the pull request anyway?",
  )
    ? true
    : null;
}

function shouldShowGitHubConnect(
  fix: FixJob,
  providerStatus: RepositoryProviderStatus | null,
) {
  if (providerStatus?.provider && providerStatus.provider !== "github") {
    return false;
  }

  if (providerStatus?.is_connected) {
    return false;
  }

  const publishError = fix.publish_error?.toLowerCase() ?? "";
  return (
    providerStatus?.provider === "github" ||
    publishError.includes("github app connection") ||
    publishError.includes("github app id") ||
    publishError.includes("installation")
  );
}

function formatValidationStatus(status: FixValidationStatus) {
  return status.replaceAll("_", " ").toLowerCase();
}

function formatProviderValue(
  fix: FixJob,
  providerStatus: RepositoryProviderStatus | null,
) {
  if (providerStatus?.is_connected) {
    return providerStatus.account_login
      ? `GitHub connected as ${providerStatus.account_login}`
      : "GitHub connected";
  }

  if (providerStatus?.provider === "github") {
    return "GitHub";
  }

  return fix.provider ?? "not selected";
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
