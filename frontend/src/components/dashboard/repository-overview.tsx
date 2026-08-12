import { ArrowRight, GitBranch } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { RepositoryOverviewItem } from "@/lib/dashboard";
import { formatRelativeTime } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

export function RepositoryOverview({
  repositories,
}: {
  repositories: RepositoryOverviewItem[];
}) {
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle>Repositories</CardTitle>
          <CardDescription>Connected sources and their review status.</CardDescription>
        </div>
        <Button asChild size="sm" variant="secondary">
          <Link href="/repositories">
            Manage
            <ArrowRight aria-hidden="true" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2.5">
          {repositories.map((repository) => (
            <li key={repository.id}>
              <Link
                className="flex items-center gap-3 rounded-md border border-border bg-background p-3 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                href={repository.href}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {repository.name}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
                    <span className="capitalize">{repository.platform}</span>
                    <span className="inline-flex items-center gap-1">
                      <GitBranch aria-hidden="true" className="size-3.5" />
                      {repository.defaultBranch}
                    </span>
                    <span>
                      {repository.lastReviewedAt
                        ? `Reviewed ${formatRelativeTime(repository.lastReviewedAt)}`
                        : "Not reviewed yet"}
                    </span>
                  </span>
                </span>
                <span
                  className={cn(
                    "shrink-0 rounded-md border px-2 py-1 text-xs font-semibold",
                    repository.hasRecentReview
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                      : "border-border bg-muted text-muted-foreground",
                  )}
                >
                  {repository.hasRecentReview ? "Reviewed" : "Pending"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
