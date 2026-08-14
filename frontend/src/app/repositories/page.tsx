"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  GitBranch,
  GitFork,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PaginationControls } from "@/components/ui/pagination-controls";
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
import { isSupportedRepositoryUrl } from "@/lib/repository-validation";
import {
  createRepository,
  deleteRepository,
  getRepositories,
} from "@/lib/repositories";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  removeRepository,
  setRepositories,
  setRepositoryError,
  setRepositoryLoading,
  setRepositoryMutating,
  upsertRepository,
} from "@/store/slices/repositorySlice";
import type { Repository } from "@/types/repository";

const repositorySchema = z.object({
  branch: z
    .string()
    .trim()
    .min(1, "Branch is required.")
    .max(100, "Branch must be 100 characters or less."),
  name: z
    .string()
    .trim()
    .min(1, "Name is required.")
    .max(255, "Name must be 255 characters or less."),
  url: z
    .string()
    .trim()
    .url("Enter a valid repository URL.")
    .refine(
      isSupportedRepositoryUrl,
      "Enter a full HTTPS GitHub or GitLab repository URL.",
    ),
});

type RepositoryFormValues = z.infer<typeof repositorySchema>;

const BRANCH_PRESETS = ["main", "master"] as const;
const REPOSITORIES_PAGE_SIZE = 10;

export default function RepositoriesPage() {
  const dispatch = useAppDispatch();
  const { error, isLoading, isMutating, items } = useAppSelector(
    (state) => state.repositories,
  );
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [deletingRepositoryId, setDeletingRepositoryId] = useState<string | null>(
    null,
  );
  const [repositoryPendingDeletion, setRepositoryPendingDeletion] =
    useState<Repository | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const pagedItems = useMemo(
    () =>
      items.slice(
        (currentPage - 1) * REPOSITORIES_PAGE_SIZE,
        currentPage * REPOSITORIES_PAGE_SIZE,
      ),
    [currentPage, items],
  );
  const form = useForm<RepositoryFormValues>({
    mode: "onChange",
    resolver: zodResolver(repositorySchema),
    defaultValues: {
      branch: "main",
      name: "",
      url: "",
    },
  });

  const loadRepositories = useCallback(async () => {
    dispatch(setRepositoryLoading(true));

    try {
      dispatch(setRepositories(await getRepositories()));
    } catch (requestError) {
      dispatch(
        setRepositoryError(
          getApiErrorMessage(requestError, "Unable to load repositories."),
        ),
      );
    } finally {
      dispatch(setRepositoryLoading(false));
    }
  }, [dispatch]);

  useEffect(() => {
    void loadRepositories();
  }, [loadRepositories]);

  useEffect(() => {
    const totalPages = Math.max(
      1,
      Math.ceil(items.length / REPOSITORIES_PAGE_SIZE),
    );
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, items.length]);

  async function handleCreateRepository(values: RepositoryFormValues) {
    dispatch(setRepositoryMutating(true));
    form.clearErrors("name");
    form.clearErrors("root");

    const hasDuplicateName = items.some(
      (repository) =>
        repository.name.toLocaleLowerCase() === values.name.toLocaleLowerCase(),
    );
    if (hasDuplicateName) {
      form.setError("name", {
        message: "A repository with this name already exists.",
        type: "duplicate",
      });
      dispatch(setRepositoryMutating(false));
      return;
    }

    try {
      const repository = await createRepository({
        default_branch: values.branch,
        name: values.name,
        url: values.url,
      });

      dispatch(upsertRepository(repository));
      toast.success("Repository added.");
      form.reset({ branch: "main", name: "", url: "" });
      setIsFormOpen(false);
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        "Unable to add repository.",
      );
      if (message === "A repository with this name already exists") {
        form.setError("name", { message, type: "server" });
      } else {
        form.setError("root", { message, type: "server" });
      }
      toast.error(message);
    } finally {
      dispatch(setRepositoryMutating(false));
    }
  }

  async function handleDeleteRepository(repository: Repository) {
    setDeletingRepositoryId(repository.id);

    try {
      await deleteRepository(repository.id);
      dispatch(removeRepository(repository.id));
      setRepositoryPendingDeletion(null);
      toast.success("Repository deleted.");
    } catch (requestError) {
      toast.error(
        getApiErrorMessage(requestError, "Unable to delete repository."),
      );
    } finally {
      setDeletingRepositoryId(null);
    }
  }

  return (
    <>
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={() => setIsFormOpen(true)}>
          <Plus aria-hidden="true" />
          Add Repository
        </Button>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>Repository List</CardTitle>
            <CardDescription>
              Source targets available for static analysis and AI review jobs.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? <RepositoryListSkeleton /> : null}
          {!isLoading && error ? (
            <div className="border-t border-border p-6">
              <p className="text-sm text-destructive">{error}</p>
            </div>
          ) : null}
          {!isLoading && !error && items.length === 0 ? (
            <EmptyRepositoryState onAdd={() => setIsFormOpen(true)} />
          ) : null}
          {!isLoading && !error && items.length > 0 ? (
            <>
              <RepositoryTable
                deletingRepositoryId={deletingRepositoryId}
                onRequestDelete={setRepositoryPendingDeletion}
                repositories={pagedItems}
              />
              <PaginationControls
                currentPage={currentPage}
                onPageChange={setCurrentPage}
                pageSize={REPOSITORIES_PAGE_SIZE}
                totalItems={items.length}
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      {isFormOpen ? (
        <div
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4 backdrop-blur-sm"
          role="dialog"
        >
          <Card className="w-full max-w-lg rounded-md">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>Add Repository</CardTitle>
                <CardDescription>
                  Connect a public GitHub or GitLab repository.
                </CardDescription>
              </div>
              <Button
                aria-label="Close add repository form"
                onClick={() => setIsFormOpen(false)}
                size="icon"
                type="button"
                variant="ghost"
              >
                <X aria-hidden="true" />
              </Button>
            </CardHeader>
            <CardContent>
              <Form {...form}>
                <form
                  className="space-y-5"
                  noValidate
                  onSubmit={form.handleSubmit(handleCreateRepository)}
                >
                  <FormField
                    control={form.control}
                    name="name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Name</FormLabel>
                        <FormControl>
                          <Input placeholder="Backend API" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="url"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>URL</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="https://github.com/org/repo"
                            type="url"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
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
                  {form.formState.errors.root?.message ? (
                    <p className="text-sm font-medium text-destructive">
                      {form.formState.errors.root.message}
                    </p>
                  ) : null}
                  <div className="flex justify-end gap-2">
                    <Button
                      onClick={() => setIsFormOpen(false)}
                      type="button"
                      variant="secondary"
                    >
                      Cancel
                    </Button>
                    <Button
                      disabled={isMutating || !form.formState.isValid}
                      type="submit"
                    >
                      {isMutating ? "Adding..." : "Add Repository"}
                    </Button>
                  </div>
                </form>
              </Form>
            </CardContent>
          </Card>
        </div>
      ) : null}

      <ConfirmationDialog
        description={
          repositoryPendingDeletion
            ? `This permanently deletes “${repositoryPendingDeletion.name}” and its review history. This action cannot be undone.`
            : ""
        }
        isPending={deletingRepositoryId !== null}
        onCancel={() => setRepositoryPendingDeletion(null)}
        onConfirm={() => {
          if (repositoryPendingDeletion) {
            void handleDeleteRepository(repositoryPendingDeletion);
          }
        }}
        open={repositoryPendingDeletion !== null}
        title="Delete repository?"
      />
    </>
  );
}

function RepositoryListSkeleton() {
  return (
    <div className="border-t border-border">
      {Array.from({ length: 4 }).map((_, index) => (
        <div
          className="grid grid-cols-1 gap-3 border-b border-border px-6 py-4 md:grid-cols-[1.3fr_1fr_0.7fr_0.5fr] md:items-center"
          key={index}
        >
          <div className="h-5 w-44 animate-pulse rounded bg-muted" />
          <div className="h-4 w-56 animate-pulse rounded bg-muted" />
          <div className="h-4 w-24 animate-pulse rounded bg-muted" />
          <div className="h-9 w-24 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

type EmptyRepositoryStateProps = {
  onAdd: () => void;
};

function EmptyRepositoryState({ onAdd }: EmptyRepositoryStateProps) {
  return (
    <div className="flex flex-col items-center border-t border-border px-6 py-12 text-center">
      <GitFork className="size-10 text-muted-foreground" aria-hidden="true" />
      <h2 className="mt-4 text-lg font-semibold tracking-normal">
        No repositories yet
      </h2>
      <p className="mt-2 max-w-md text-[15px] leading-6 text-muted-foreground">
        Connect a GitHub or GitLab repository to start building review history.
      </p>
      <Button className="mt-5" onClick={onAdd}>
        <Plus aria-hidden="true" />
        Add Repository
      </Button>
    </div>
  );
}

type RepositoryTableProps = {
  deletingRepositoryId: string | null;
  onRequestDelete: (repository: Repository) => void;
  repositories: Repository[];
};

function RepositoryTable({
  deletingRepositoryId,
  onRequestDelete,
  repositories,
}: RepositoryTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto border-t border-border">
      <table className="w-full min-w-[760px] text-left text-[15px]">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-6 py-3 font-medium">Name</th>
            <th className="px-6 py-3 font-medium">URL</th>
            <th className="px-6 py-3 font-medium">Branch</th>
            <th className="px-6 py-3 font-medium">Platform</th>
            <th className="px-6 py-3 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {repositories.map((repository) => (
            <tr
              aria-label={`Open ${repository.name}`}
              className="cursor-pointer border-t border-border transition-colors hover:bg-muted/35 focus-visible:bg-muted/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              key={repository.id}
              onClick={() => router.push(`/repositories/${repository.id}`)}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget || event.key !== "Enter") {
                  return;
                }

                router.push(`/repositories/${repository.id}`);
              }}
              role="link"
              tabIndex={0}
            >
              <td className="px-6 py-4">
                <span className="font-medium text-foreground">
                  {repository.name}
                </span>
              </td>
              <td className="max-w-[280px] truncate px-6 py-4 text-muted-foreground">
                {repository.url}
              </td>
              <td className="px-6 py-4">
                <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">
                  <GitBranch aria-hidden="true" className="size-3" />
                  {repository.default_branch}
                </span>
              </td>
              <td className="px-6 py-4 capitalize text-muted-foreground">
                {repository.platform ?? "unknown"}
              </td>
              <td className="px-6 py-4 text-right">
                <Button
                  aria-label={`Delete ${repository.name}`}
                  disabled={deletingRepositoryId === repository.id}
                  onClick={(event) => {
                    event.stopPropagation();
                    onRequestDelete(repository);
                  }}
                  size="icon"
                  variant="ghost"
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
