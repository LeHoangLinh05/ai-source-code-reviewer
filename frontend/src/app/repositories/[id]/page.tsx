"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  ArrowLeft,
  CalendarClock,
  ExternalLink,
  GitBranch,
  Play,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
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
import { deleteRepository, getRepository } from "@/lib/repositories";
import { createReviewJob } from "@/lib/review-jobs";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  removeRepository,
  setRepositoryError,
  setRepositoryLoading,
  setRepositoryMutating,
  setSelectedRepository,
  upsertRepository,
} from "@/store/slices/repositorySlice";

const startReviewSchema = z.object({
  branch: z.string().trim().min(1, "Branch is required.").max(100),
  runStaticAnalysis: z.boolean(),
});

type StartReviewFormValues = z.infer<typeof startReviewSchema>;

export default function RepositoryDetailPage() {
  const params = useParams<{ id: string }>();
  const repositoryId = params.id;
  const dispatch = useAppDispatch();
  const router = useRouter();
  const { error, isLoading, isMutating, selectedRepository } = useAppSelector(
    (state) => state.repositories,
  );
  const [isDeleting, setIsDeleting] = useState(false);
  const [isStartReviewOpen, setIsStartReviewOpen] = useState(false);
  const [isStartingReview, setIsStartingReview] = useState(false);
  const startReviewForm = useForm<StartReviewFormValues>({
    resolver: zodResolver(startReviewSchema),
    defaultValues: {
      branch: "main",
      runStaticAnalysis: true,
    },
  });
  const createdAt = useMemo(() => {
    if (!selectedRepository?.created_at) {
      return null;
    }

    return new Intl.DateTimeFormat("en", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(selectedRepository.created_at));
  }, [selectedRepository?.created_at]);

  async function loadRepository() {
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
  }

  useEffect(() => {
    void loadRepository();

    return () => {
      dispatch(setSelectedRepository(null));
    };
  }, [repositoryId]);

  async function handleDeleteRepository() {
    if (!selectedRepository) {
      return;
    }

    setIsDeleting(true);
    dispatch(setRepositoryMutating(true));

    try {
      await deleteRepository(selectedRepository.id);
      dispatch(removeRepository(selectedRepository.id));
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
      runStaticAnalysis: true,
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
          run_static_analysis: values.runStaticAnalysis,
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
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <Button asChild className="mb-4" size="sm" variant="ghost">
            <Link href="/repositories">
              <ArrowLeft aria-hidden="true" />
              Repositories
            </Link>
          </Button>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Repository target
          </p>
          <h1 className="mt-2 text-2xl font-extrabold tracking-normal">
            {selectedRepository?.name ?? "Repository Details"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Metadata, default branch, and review launch controls.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={isLoading}
            onClick={() => void loadRepository()}
            variant="secondary"
          >
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </header>

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
          <section className="grid gap-4 md:grid-cols-3">
            <InfoCard
              label="Platform"
              value={selectedRepository.platform ?? "unknown"}
            />
            <InfoCard label="Branch" value={selectedRepository.default_branch} />
            <InfoCard label="Created" value={createdAt ?? "Not available"} />
          </section>

          <Card>
            <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>Repository</CardTitle>
                <CardDescription>
                  Owner-scoped source repository details.
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button onClick={openStartReviewModal}>
                  <Play aria-hidden="true" />
                  Start Review
                </Button>
                <Button
                  disabled={isDeleting || isMutating}
                  onClick={() => void handleDeleteRepository()}
                  variant="destructive"
                >
                  <Trash2 aria-hidden="true" />
                  Delete
                </Button>
              </div>
            </CardHeader>
            <CardContent className="grid gap-5">
              <DetailRow label="Name" value={selectedRepository.name} />
              <DetailRow
                label="URL"
                value={
                  <a
                    className="inline-flex min-w-0 items-center gap-2 text-slate-300 hover:underline"
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
                value={selectedRepository.last_reviewed_at ?? "No reviews yet"}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Review History</CardTitle>
              <CardDescription>
                Review jobs for this repository will appear here.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-col items-center rounded-md border border-dashed border-slate-700 bg-background px-6 py-10 text-center">
                <CalendarClock
                  aria-hidden="true"
                  className="size-10 text-muted-foreground"
                />
                <h2 className="mt-4 text-lg font-semibold tracking-normal">
                  No review history yet
                </h2>
                <p className="mt-2 max-w-md text-sm text-muted-foreground">
                  Review job history will be connected in the review_jobs slice.
                </p>
              </div>
            </CardContent>
          </Card>
        </>
      ) : null}

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
                        <FormControl>
                          <Input placeholder="main" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={startReviewForm.control}
                    name="runStaticAnalysis"
                    render={({ field }) => (
                      <FormItem>
                        <div className="flex items-center justify-between rounded-md border border-border p-3">
                          <FormLabel>Run static analysis</FormLabel>
                          <FormControl>
                            <input
                              checked={field.value}
                              className="size-4 accent-primary"
                              onChange={(event) =>
                                field.onChange(event.target.checked)
                              }
                              type="checkbox"
                            />
                          </FormControl>
                        </div>
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

type InfoCardProps = {
  label: string;
  value: string;
};

function InfoCard({ label, value }: InfoCardProps) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-xs font-medium uppercase text-muted-foreground">
          {label}
        </p>
        <p className="mt-2 text-lg font-semibold capitalize tracking-normal">
          {value}
        </p>
      </CardContent>
    </Card>
  );
}

type DetailRowProps = {
  label: string;
  value: React.ReactNode;
};

function DetailRow({ label, value }: DetailRowProps) {
  return (
    <div className="grid gap-2 border-b border-border pb-4 last:border-b-0 last:pb-0 sm:grid-cols-[180px_1fr]">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-sm text-foreground">{value}</dd>
    </div>
  );
}

function RepositoryDetailSkeleton() {
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
