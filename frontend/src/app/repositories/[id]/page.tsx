"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  ArrowLeft,
  CalendarClock,
  ExternalLink,
  GitBranch,
  Play,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { StatusBadge } from "@/components/reviews/review-badges";
import { Button } from "@/components/ui/button";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  deleteRepository,
  getRepository,
  getRepositorySummary,
} from "@/lib/repositories";
import { getReport } from "@/lib/reports";
import { createReviewJob, getReviewJobs } from "@/lib/review-jobs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  removeRepository,
  setRepositoryError,
  setRepositoryLoading,
  setRepositoryMutating,
  setSelectedRepository,
  upsertRepository,
} from "@/store/slices/repositorySlice";
import type { ReviewJob, ReviewJobStatus } from "@/types/review-job";
import type { ReviewReport } from "@/types/report";
import type { RepoSummary } from "@/types/repository";

const startReviewSchema = z.object({
  branch: z.string().trim().min(1, "Branch is required.").max(100),
});

type StartReviewFormValues = z.infer<typeof startReviewSchema>;

const BRANCH_PRESETS = ["main", "master"] as const;
const ROADMAP_RULE_PROFILE_ID = "roadmap_bootcamp_v1";
const REVIEW_JOB_POLLING_INTERVAL_MS = 5_000;
const TERMINAL_REVIEW_STATUSES = new Set<ReviewJobStatus>([
  "COMPLETED",
  "FAILED",
]);

export default function RepositoryDetailPage() {
  const params = useParams<{ id: string }>();
  const repositoryId = params.id;
  const dispatch = useAppDispatch();
  const router = useRouter();
  const { error, isLoading, isMutating, selectedRepository } = useAppSelector(
    (state) => state.repositories,
  );
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDeleteConfirmationOpen, setIsDeleteConfirmationOpen] =
    useState(false);
  const [isStartReviewOpen, setIsStartReviewOpen] = useState(false);
  const [isStartingReview, setIsStartingReview] = useState(false);
  const [areReviewJobsLoading, setAreReviewJobsLoading] = useState(false);
  const [reviewJobs, setReviewJobs] = useState<ReviewJob[]>([]);
  const [reviewJobsError, setReviewJobsError] = useState<string | null>(null);
  const [latestReport, setLatestReport] = useState<ReviewReport | null>(null);
  const [latestReportError, setLatestReportError] = useState<string | null>(
    null,
  );
  const [isLatestReportLoading, setIsLatestReportLoading] = useState(false);
  const [repoSummary, setRepoSummary] = useState<RepoSummary | null>(null);
  const [isRepoSummaryLoading, setIsRepoSummaryLoading] = useState(false);
  const [repoSummaryError, setRepoSummaryError] = useState<string | null>(null);
  const startReviewForm = useForm<StartReviewFormValues>({
    resolver: zodResolver(startReviewSchema),
    defaultValues: {
      branch: "main",
    },
  });
  const lastReviewedAt = useMemo(() => {
    if (selectedRepository?.last_reviewed_at) {
      return selectedRepository.last_reviewed_at;
    }

    return getLatestCompletedReviewJob(reviewJobs)?.completed_at ?? null;
  }, [reviewJobs, selectedRepository?.last_reviewed_at]);
  const latestCompletedReviewJob = useMemo(
    () => getLatestCompletedReviewJob(reviewJobs),
    [reviewJobs],
  );
  const hasActiveReviewJobs = useMemo(
    () => reviewJobs.some((job) => !TERMINAL_REVIEW_STATUSES.has(job.status)),
    [reviewJobs],
  );

  const loadRepository = useCallback(async () => {
    dispatch(setRepositoryLoading(true));

    try {
      const repository = await getRepository(repositoryId);
      dispatch(setSelectedRepository(repository));
      dispatch(upsertRepository(repository));
    } catch (requestError) {
      dispatch(
        setRepositoryError(
          getApiErrorMessage(requestError, "Unable to load repository."),
        ),
      );
    } finally {
      dispatch(setRepositoryLoading(false));
    }
  }, [dispatch, repositoryId]);

  const loadLatestReport = useCallback(async (
    jobs: ReviewJob[],
    isBackground = false,
  ) => {
    if (!isBackground) {
      setLatestReport(null);
      setLatestReportError(null);
      setIsLatestReportLoading(false);
    }

    const completedJob = getLatestCompletedReviewJob(jobs);
    if (!completedJob) {
      return;
    }

    if (!isBackground) {
      setIsLatestReportLoading(true);
    }

    try {
      setLatestReport(await getReport(completedJob.id));
    } catch (requestError) {
      if (!isBackground) {
        setLatestReportError(
          getApiErrorMessage(requestError, "Unable to load latest report."),
        );
      }
    } finally {
      if (!isBackground) {
        setIsLatestReportLoading(false);
      }
    }
  }, []);

  const loadReviewJobs = useCallback(async (isBackground = false) => {
    if (!isBackground) {
      setAreReviewJobsLoading(true);
      setReviewJobsError(null);
    }

    try {
      const jobs = await getReviewJobs({ repository_id: repositoryId });
      setReviewJobs(jobs);
      void loadLatestReport(jobs, isBackground);
    } catch (requestError) {
      if (!isBackground) {
        setReviewJobsError(
          getApiErrorMessage(requestError, "Unable to load review history."),
        );
        setLatestReport(null);
      }
    } finally {
      if (!isBackground) {
        setAreReviewJobsLoading(false);
      }
    }
  }, [loadLatestReport, repositoryId]);

  const loadRepositorySummary = useCallback(async () => {
    setIsRepoSummaryLoading(true);
    setRepoSummaryError(null);

    try {
      setRepoSummary(await getRepositorySummary(repositoryId));
    } catch (requestError) {
      setRepoSummaryError(
        getApiErrorMessage(
          requestError,
          "Unable to load repository summary.",
        ),
      );
    } finally {
      setIsRepoSummaryLoading(false);
    }
  }, [repositoryId]);

  useEffect(() => {
    void loadRepository();
    void loadReviewJobs();
    void loadRepositorySummary();

    return () => {
      dispatch(setSelectedRepository(null));
    };
  }, [dispatch, loadRepository, loadRepositorySummary, loadReviewJobs]);

  useEffect(() => {
    if (!hasActiveReviewJobs) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadReviewJobs(true);
    }, REVIEW_JOB_POLLING_INTERVAL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [hasActiveReviewJobs, loadReviewJobs]);


  async function handleDeleteRepository() {
    if (!selectedRepository) {
      return;
    }

    setIsDeleting(true);
    dispatch(setRepositoryMutating(true));

    try {
      await deleteRepository(selectedRepository.id);
      dispatch(removeRepository(selectedRepository.id));
      setIsDeleteConfirmationOpen(false);
      toast.success("Repository deleted.");
      router.replace("/repositories");
    } catch (requestError) {
      toast.error(
        getApiErrorMessage(requestError, "Unable to delete repository."),
      );
    } finally {
      setIsDeleting(false);
      dispatch(setRepositoryMutating(false));
    }
  }

  function openStartReviewModal() {
    startReviewForm.reset({
      branch: selectedRepository?.default_branch ?? "main",
    });
    setIsStartReviewOpen(true);
  }

  async function handleStartReview(values: StartReviewFormValues) {
    if (!selectedRepository) {
      return;
    }

    setIsStartingReview(true);
    startReviewForm.clearErrors("root");

    try {
      const response = await createReviewJob({
        branch: values.branch,
        options: {
          review_mode: "full_audit",
          rule_profile: {
            id: ROADMAP_RULE_PROFILE_ID,
          },
        },
        repository_id: selectedRepository.id,
      });

      toast.success("Review job created.");
      router.push(`/reviews/${response.job_id}`);
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        "Unable to start review job.",
      );
      startReviewForm.setError("root", { message, type: "server" });
      toast.error(message);
    } finally {
      setIsStartingReview(false);
    }
  }

  return (
    <>
      <div className="sticky top-0 z-20 flex flex-col gap-3 border-b border-border bg-background/95 pb-4 pt-1 backdrop-blur md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href="/repositories">
            <ArrowLeft aria-hidden="true" />
            Repositories
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2">
          {selectedRepository ? (
            <Button onClick={openStartReviewModal}>
              <Play aria-hidden="true" />
              Start Review
            </Button>
          ) : null}
          {selectedRepository ? (
            <Button
              disabled={isDeleting || isMutating}
              onClick={() => setIsDeleteConfirmationOpen(true)}
              variant="destructive"
            >
              <Trash2 aria-hidden="true" />
              Delete
            </Button>
          ) : null}
        </div>
      </div>

      {isLoading ? <RepositoryDetailSkeleton /> : null}

      {!isLoading && error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {!isLoading && !error && selectedRepository ? (
        <>
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Repository</CardTitle>
                <CardDescription>
                  Owner-scoped source repository details.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-5">
              <DetailRow label="Name" value={selectedRepository.name} />
              <DetailRow
                label="URL"
                value={
                  <a
                    className="inline-flex min-w-0 items-center gap-2 text-primary hover:underline"
                    href={selectedRepository.url}
                    rel="noreferrer"
                    target="_blank"
                  >
                    <span className="truncate">{selectedRepository.url}</span>
                    <ExternalLink aria-hidden="true" className="size-4" />
                  </a>
                }
              />
              <DetailRow
                label="Default branch"
                value={
                  <span className="inline-flex items-center gap-2">
                    <GitBranch aria-hidden="true" className="size-4" />
                    {selectedRepository.default_branch}
                  </span>
                }
              />
              <DetailRow
                label="Last reviewed"
                value={
                  lastReviewedAt !== null
                    ? formatDateTime(lastReviewedAt)
                    : "No reviews yet"
                }
              />
            </CardContent>
          </Card>

          <section className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
            <Card>
              <CardHeader>
                <CardTitle>Latest Report Summary</CardTitle>
                <CardDescription>
                  Latest completed review signal for this repository.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <LatestReportSummary
                  error={latestReportError}
                  isLoading={isLatestReportLoading}
                  latestJob={latestCompletedReviewJob}
                  report={latestReport}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Project Overview</CardTitle>
                <CardDescription>
                  Project overview generated from repository structure and setup
                  files.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <RepositorySummaryCard
                  error={repoSummaryError}
                  isLoading={isRepoSummaryLoading}
                  summary={repoSummary}
                />
              </CardContent>
            </Card>
          </section>

          <Card>
            <CardHeader>
              <CardTitle>Review History</CardTitle>
              <CardDescription>
                Review jobs for this repository will appear here.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ReviewHistory
                error={reviewJobsError}
                isLoading={areReviewJobsLoading}
                jobs={reviewJobs}
              />
            </CardContent>
          </Card>
        </>
      ) : null}

      <ConfirmationDialog
        description={
          selectedRepository
            ? `This permanently deletes “${selectedRepository.name}” and its review history. This action cannot be undone.`
            : ""
        }
        isPending={isDeleting}
        onCancel={() => setIsDeleteConfirmationOpen(false)}
        onConfirm={() => void handleDeleteRepository()}
        open={isDeleteConfirmationOpen && selectedRepository !== null}
        title="Delete repository?"
      />

      {isStartReviewOpen && selectedRepository ? (
        <div
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4 backdrop-blur-sm"
          role="dialog"
        >
          <Card className="w-full max-w-lg rounded-md">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>Start Review</CardTitle>
                <CardDescription>
                  Create a review job for {selectedRepository.name}.
                </CardDescription>
              </div>
              <Button
                aria-label="Close start review form"
                onClick={() => setIsStartReviewOpen(false)}
                size="icon"
                type="button"
                variant="ghost"
              >
                <X aria-hidden="true" />
              </Button>
            </CardHeader>
            <CardContent>
              <Form {...startReviewForm}>
                <form
                  className="space-y-5"
                  onSubmit={startReviewForm.handleSubmit(handleStartReview)}
                >
                  <FormField
                    control={startReviewForm.control}
                    name="branch"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Branch</FormLabel>
                        <div className="flex flex-wrap gap-2">
                          {BRANCH_PRESETS.map((branch) => (
                            <Button
                              key={branch}
                              onClick={() => field.onChange(branch)}
                              size="sm"
                              type="button"
                              variant={
                                field.value === branch ? "secondary" : "outline"
                              }
                            >
                              {branch}
                            </Button>
                          ))}
                        </div>
                        <FormControl>
                          <Input placeholder="feature/custom-branch" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {startReviewForm.formState.errors.root?.message ? (
                    <p className="text-sm font-medium text-destructive">
                      {startReviewForm.formState.errors.root.message}
                    </p>
                  ) : null}
                  <div className="flex justify-end gap-2">
                    <Button
                      onClick={() => setIsStartReviewOpen(false)}
                      type="button"
                      variant="secondary"
                    >
                      Cancel
                    </Button>
                    <Button disabled={isStartingReview} type="submit">
                      {isStartingReview ? "Starting..." : "Start Review"}
                    </Button>
                  </div>
                </form>
              </Form>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  );
}

type DetailRowProps = {
  label: string;
  value: React.ReactNode;
};

function DetailRow({ label, value }: DetailRowProps) {
  return (
    <div className="grid gap-2 border-b border-border pb-4 last:border-b-0 last:pb-0 sm:grid-cols-[180px_1fr]">
      <dt className="text-[15px] text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-[15px] text-foreground">{value}</dd>
    </div>
  );
}

type LatestReportSummaryProps = {
  error: string | null;
  isLoading: boolean;
  latestJob: ReviewJob | null;
  report: ReviewReport | null;
};

function LatestReportSummary({
  error,
  isLoading,
  latestJob,
  report,
}: LatestReportSummaryProps) {
  if (isLoading) {
    return <LatestReportSummarySkeleton />;
  }

  if (error !== null) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  if (report === null || latestJob === null) {
    return (
      <div className="rounded-md border border-dashed border-border bg-background px-6 py-8 text-center">
        <h2 className="text-lg font-semibold tracking-normal">
          No completed report yet
        </h2>
        <p className="mx-auto mt-2 max-w-md text-[15px] leading-6 text-muted-foreground">
          Start a review and this panel will show the latest confirmed findings.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <ReportMetric label="Total findings" value={report.total_findings} />
        <ReportMetric label="Critical" value={report.critical_count} />
        <ReportMetric label="High" value={report.high_count} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-sm text-muted-foreground">
          Completed {formatOptionalDate(latestJob.completed_at)}
        </p>
        <Button asChild size="sm" variant="secondary">
          <Link href={`/reviews/${latestJob.id}/report`}>Open report</Link>
        </Button>
      </div>
    </div>
  );
}

function ReportMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-2xl font-extrabold tracking-normal">{value}</p>
    </div>
  );
}

function LatestReportSummarySkeleton() {
  return (
    <div className="grid gap-4">
      <div className="h-14 animate-pulse rounded bg-muted" />
      <div className="grid gap-3 sm:grid-cols-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <div className="h-20 animate-pulse rounded bg-muted" key={index} />
        ))}
      </div>
    </div>
  );
}

type RepositorySummaryCardProps = {
  error: string | null;
  isLoading: boolean;
  summary: RepoSummary | null;
};

function RepositorySummaryCard({
  error,
  isLoading,
  summary,
}: RepositorySummaryCardProps) {
  if (isLoading) {
    return <RepositorySummarySkeleton />;
  }

  if (error !== null) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  if (summary === null) {
    return (
      <div className="rounded-md border border-dashed border-border bg-background px-6 py-8 text-center">
        <h2 className="text-lg font-semibold tracking-normal">
          No project overview yet
        </h2>
        <p className="mx-auto mt-2 max-w-md text-[15px] leading-6 text-muted-foreground">
          A project overview will be generated after the first review completes.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      <div>
        <p className="text-xs font-medium uppercase text-muted-foreground">
          Purpose
        </p>
        <p className="mt-2 text-[15px] leading-6 text-foreground">
          {summary.purpose}
        </p>
      </div>

      <div>
        <p className="text-xs font-medium uppercase text-muted-foreground">
          Architecture
        </p>
        <p className="mt-2 text-[15px] leading-6 text-foreground">
          {summary.architecture_overview}
        </p>
      </div>

      <SummaryChipList items={summary.tech_stack} label="Tech stack" />
    </div>
  );
}

type SummaryChipListProps = {
  items: string[];
  label: string;
};

function SummaryChipList({ items, label }: SummaryChipListProps) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      {items.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {items.map((item) => (
            <span
              className="rounded-md border border-border bg-background px-2 py-1 text-xs font-medium text-foreground"
              key={item}
            >
              {item}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">None detected.</p>
      )}
    </div>
  );
}

function RepositorySummarySkeleton() {
  return (
    <div className="grid gap-5">
      <div className="h-5 w-40 animate-pulse rounded bg-muted" />
      <div className="h-20 animate-pulse rounded bg-muted" />
      <div className="h-16 animate-pulse rounded bg-muted" />
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: 4 }).map((_, index) => (
          <div
            className="h-7 w-20 animate-pulse rounded-md bg-muted"
            key={index}
          />
        ))}
      </div>
    </div>
  );
}

type ReviewHistoryProps = {
  error: string | null;
  isLoading: boolean;
  jobs: ReviewJob[];
};

function ReviewHistory({ error, isLoading, jobs }: ReviewHistoryProps) {
  if (isLoading) {
    return <ReviewHistorySkeleton />;
  }

  if (error !== null) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center rounded-md border border-dashed border-border bg-background px-6 py-10 text-center">
        <CalendarClock
          aria-hidden="true"
          className="size-10 text-muted-foreground"
        />
        <h2 className="mt-4 text-lg font-semibold tracking-normal">
          No review history yet
        </h2>
        <p className="mt-2 max-w-md text-[15px] leading-6 text-muted-foreground">
          Start a review to track status, branch, and completion time here.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {jobs.map((job) => (
        <div
          className="grid gap-3 rounded-md border border-border bg-background p-4 md:grid-cols-[1fr_auto] md:items-center"
          key={job.id}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                className="min-w-0 truncate text-[15px] font-semibold text-foreground hover:text-primary/80"
                href={`/reviews/${job.id}`}
              >
                Review {job.id.slice(0, 8)}
              </Link>
              <StatusBadge status={job.status} />
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <GitBranch aria-hidden="true" className="size-3.5" />
                {job.branch ?? "main"}
              </span>
              <span>Created {formatDateTime(job.created_at)}</span>
              {job.completed_at !== null ? (
                <span>Completed {formatDateTime(job.completed_at)}</span>
              ) : null}
            </div>
            {job.error_message !== null ? (
              <p className="mt-2 text-sm text-destructive">{job.error_message}</p>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2 md:justify-end">
            <Button asChild size="sm" variant="secondary">
              <Link href={`/reviews/${job.id}`}>Open</Link>
            </Button>
            {job.status === "COMPLETED" ? (
              <Button asChild size="sm" variant="outline">
                <Link href={`/reviews/${job.id}/report`}>Report</Link>
              </Button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function ReviewHistorySkeleton() {
  return (
    <div className="grid gap-3">
      {Array.from({ length: 3 }).map((_, index) => (
        <div
          className="rounded-md border border-border bg-background p-4"
          key={index}
        >
          <div className="h-5 w-48 animate-pulse rounded bg-muted" />
          <div className="mt-3 h-4 w-72 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function RepositoryDetailSkeleton() {
  return (
    <div className="grid gap-4">
      <Card>
        <CardContent className="space-y-4 p-6">
          {Array.from({ length: 4 }).map((_, index) => (
            <div className="h-5 animate-pulse rounded bg-muted" key={index} />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatOptionalDate(value: string | null) {
  return value === null ? "Not available" : formatDateTime(value);
}

function getLatestCompletedReviewJob(jobs: ReviewJob[]) {
  return (
    [...jobs]
      .filter((job) => job.status === "COMPLETED" && job.completed_at !== null)
      .sort(compareReviewJobsByLatest)[0] ?? null
  );
}

function compareReviewJobsByLatest(leftJob: ReviewJob, rightJob: ReviewJob) {
  return getReviewJobTimestamp(rightJob) - getReviewJobTimestamp(leftJob);
}

function getReviewJobTimestamp(job: ReviewJob) {
  return new Date(job.completed_at ?? job.created_at).getTime();
}
