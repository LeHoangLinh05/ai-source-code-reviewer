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
import { Input } from "@/components/ui/input";
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
  getUnresolvedFixResults,
  isFixLive,
  requiresFailedValidationOverride,
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
import { getRepositoryProviderStatus } from "@/lib/providers";
import { getReviewJob } from "@/lib/review-jobs";
import type {
  FixDiff,
  FixJob,
  FixJobProgressEvent,
  FixJobStatus,
  FixIssueResult,
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
    "publish" | "fork" | "retry" | "cancel" | null
  >(null);
  const [selectedFixId, setSelectedFixId] = useState<string | null>(null);
  const [isOverrideOpen, setIsOverrideOpen] = useState(false);
  const [isOverrideConfirmed, setIsOverrideConfirmed] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");

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

    if (requiresFailedValidationOverride(selectedFix)) {
      setIsOverrideConfirmed(false);
      setOverrideReason("");
      setIsOverrideOpen(true);
      return;
    }

    await performPublish(false, null);
  }

  async function performPublish(
    allowFailedValidation: boolean,
    reason: string | null,
  ) {
    if (!selectedFix || !canPublishFix(selectedFix)) {
      return;
    }

    setPendingAction("fork");
    setIsOverrideOpen(false);

    try {
      const updatedFix = await publishFixJob(selectedFix.id, {
        allow_failed_validation: allowFailedValidation,
        override_reason: allowFailedValidation ? reason : null,
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

  return (
    <>
      <div className="flex items-center">
        <Button asChild size="sm" variant="ghost">
          <Link href={`/reviews/${jobId}`}>
            <ArrowLeft aria-hidden="true" />
            Review Job
          </Link>
        </Button>
      </div>

      <ReviewWorkspaceTabs activeTab="fixes" jobId={jobId} />

      {isOverrideOpen && selectedFix ? (
        <FailedValidationOverrideDialog
          fix={selectedFix}
          isConfirmed={isOverrideConfirmed}
          overrideReason={overrideReason}
          onCancel={() => setIsOverrideOpen(false)}
          onConfirm={() => void performPublish(true, overrideReason.trim())}
          onConfirmationChange={setIsOverrideConfirmed}
          onReasonChange={setOverrideReason}
        />
      ) : null}

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
                    isActionPending={pendingAction !== null}
                    isDiffLoading={isDiffLoading}
                    jobId={jobId}
                    onCancelPublish={() => void cancelSelectedPublish()}
                    providerStatus={providerStatus}
                    onPublishViaFork={() => void publishSelectedFix()}
                    onRetryPublish={() => void retrySelectedPublish()}
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
  isActionPending,
  isDiffLoading,
  jobId,
  onCancelPublish,
  providerStatus,
  onPublishViaFork,
  onRetryPublish,
  progressEvent,
}: {
  connectionState: FixJobProgressConnectionState;
  diff: FixDiff | null;
  fix: FixJob;
  isActionPending: boolean;
  isDiffLoading: boolean;
  jobId: string;
  onCancelPublish: () => void;
  providerStatus: RepositoryProviderStatus | null;
  onPublishViaFork: () => void;
  onRetryPublish: () => void;
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
        isActionPending={isActionPending}
        jobId={jobId}
        onCancelPublish={onCancelPublish}
        providerStatus={providerStatus}
        onPublishViaFork={onPublishViaFork}
        onRetryPublish={onRetryPublish}
      />

      <Card>
        <CardHeader>
          <CardTitle>Patch Preview</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4">
          {failureReason ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <AlertTriangle aria-hidden="true" className="mr-2 inline size-4" />
              {failureReason}
            </p>
          ) : null}

          {fix.changed_files?.length ? (
            <div className="grid gap-2">
              <p className="text-sm font-semibold">
                Changed Files ({fix.changed_files.length})
              </p>
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

      <TechnicalDetails title="Logic review by issue">
        <IssueVerificationResults results={fix.issue_results} />
      </TechnicalDetails>
    </div>
  );
}

function FailedValidationOverrideDialog({
  fix,
  isConfirmed,
  overrideReason,
  onCancel,
  onConfirm,
  onConfirmationChange,
  onReasonChange,
}: {
  fix: FixJob;
  isConfirmed: boolean;
  overrideReason: string;
  onCancel: () => void;
  onConfirm: () => void;
  onConfirmationChange: (value: boolean) => void;
  onReasonChange: (value: string) => void;
}) {
  const unresolvedResults = getUnresolvedFixResults(fix);
  const failedChecks =
    fix.validation_summary?.checks.filter((check) => check.status === "failed") ?? [];

  return (
    <div
      aria-labelledby="failed-validation-title"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
    >
      <div className="grid max-h-[85dvh] w-full max-w-xl gap-4 overflow-y-auto rounded-md border border-border bg-card p-5 shadow-xl">
        <div>
          <h2 className="text-lg font-semibold" id="failed-validation-title">
            Publish with failed verification
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            This patch has unresolved checks. Publishing it may leave selected
            issues unfixed.
          </p>
        </div>

        {unresolvedResults.length ? (
          <div className="grid gap-2">
            <p className="text-sm font-semibold">Unresolved issues</p>
            {unresolvedResults.map((result) => (
              <div className="rounded-md border border-border p-3" key={result.issue_id}>
                <p className="break-all font-mono text-xs">{result.issue_id}</p>
                <p className="mt-1 text-sm text-muted-foreground">{result.summary}</p>
              </div>
            ))}
          </div>
        ) : null}

        {failedChecks.length ? (
          <div className="grid gap-1 text-sm">
            <p className="font-semibold">Failed checks</p>
            {failedChecks.map((check) => (
              <p className="font-mono text-xs text-muted-foreground" key={`${check.name}:${check.command}`}>
                {check.name}
              </p>
            ))}
          </div>
        ) : null}

        <label className="grid gap-2 text-sm font-medium">
          Override reason
          <Input
            maxLength={500}
            onChange={(event) => onReasonChange(event.target.value)}
            placeholder="Explain why publishing this unverified patch is necessary"
            value={overrideReason}
          />
          <span className="text-xs font-normal text-muted-foreground">
            Minimum 20 characters. This reason is recorded in the audit log and PR.
          </span>
        </label>

        <label className="flex items-start gap-3 rounded-md border border-destructive/30 p-3 text-sm">
          <input
            checked={isConfirmed}
            className="mt-0.5 size-4"
            onChange={(event) => onConfirmationChange(event.target.checked)}
            type="checkbox"
          />
          I understand that this pull request contains unresolved verification
          results.
        </label>

        <div className="flex justify-end gap-2">
          <Button onClick={onCancel} variant="secondary">
            Cancel
          </Button>
          <Button
            disabled={!isConfirmed || overrideReason.trim().length < 20}
            onClick={onConfirm}
            variant="destructive"
          >
            Publish anyway
          </Button>
        </div>
      </div>
    </div>
  );
}

function IssueVerificationResults({ results }: { results: FixIssueResult[] }) {
  if (results.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Logic review has not produced a result yet.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {results.map((result) => (
        <div className="rounded-md border border-border bg-background p-4" key={result.issue_id}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-semibold">
                {result.probe_id ?? "Selected finding"}
              </p>
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {result.issue_id}
              </p>
            </div>
            <span className={`rounded-md border px-2 py-1 text-xs font-semibold ${getIssueVerdictTone(result.verdict)}`}>
              {result.verdict}
            </span>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">{result.summary}</p>
          {result.scenario_results.length ? (
            <div className="mt-3 grid gap-2 border-t border-border pt-3">
              {result.scenario_results.map((scenario) => (
                <div
                  className="grid gap-1 text-xs sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                  key={scenario.scenario_id}
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono">{scenario.scenario_id}</p>
                    <p className="text-muted-foreground">
                      {scenario.kind.replaceAll("_", " ")}
                      {scenario.framework ? ` | ${scenario.framework}` : ""}
                    </p>
                  </div>
                  <p className="font-mono text-muted-foreground">
                    {scenario.baseline_status} -&gt; {scenario.patched_status}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">
            <span>{result.changed_files.length} changed files</span>
            <span>{result.verification_attempts} verification attempts</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function PublishStatusCard({
  fix,
  isActionPending,
  jobId,
  onCancelPublish,
  providerStatus,
  onPublishViaFork,
  onRetryPublish,
}: {
  fix: FixJob;
  isActionPending: boolean;
  jobId: string;
  onCancelPublish: () => void;
  providerStatus: RepositoryProviderStatus | null;
  onPublishViaFork: () => void;
  onRetryPublish: () => void;
}) {
  const publishError = fix.publish_error;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Publish</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 rounded-md border border-border bg-background p-4 sm:grid-cols-2">
          <Metric
            label="Publish status"
            value={formatFixPublishStatus(fix.publish_status)}
          />
          <Metric
            label="Published branch"
            value={fix.published_branch ?? fix.fix_branch}
          />
        </div>

        <div className="flex flex-wrap gap-2">
          {canPublishViaFork(fix) ? (
            <Button disabled={isActionPending} onClick={onPublishViaFork}>
              <GitPullRequest aria-hidden="true" />
              Publish
            </Button>
          ) : null}
          {canRetryPublish(fix) ? (
            <Button
              disabled={isActionPending}
              onClick={onRetryPublish}
              variant="secondary"
            >
              <RotateCcw aria-hidden="true" />
              Retry publish
            </Button>
          ) : null}
          {canCancelPublish(fix) ? (
            <Button
              disabled={isActionPending}
              onClick={onCancelPublish}
              variant="destructive"
            >
              <XCircle aria-hidden="true" />
              Cancel publish
            </Button>
          ) : null}
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

        {providerStatus && !providerStatus.can_publish ? (
          <p className="rounded-md border border-amber-400/40 bg-amber-400/10 p-4 text-sm text-amber-900 dark:text-amber-100">
            <AlertTriangle aria-hidden="true" className="mr-2 inline size-4" />
            {providerStatus.message}
          </p>
        ) : null}

      </CardContent>
    </Card>
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

function getIssueVerdictTone(verdict: FixIssueResult["verdict"]) {
  if (verdict === "fixed") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  }
  if (verdict === "unresolved") {
    return "border-destructive/40 bg-destructive/10 text-destructive";
  }

  return "border-amber-400/40 bg-amber-400/10 text-amber-800 dark:text-amber-100";
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
