import { CircleDot, GitBranch } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/reviews/review-badges";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { ActiveReview } from "@/lib/dashboard";
import { formatRelativeTime } from "@/lib/dashboard";

export function ActiveReviews({ reviews }: { reviews: ActiveReview[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Active reviews</CardTitle>
        <CardDescription>Reviews currently running in your workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        {reviews.length === 0 ? (
          <p className="flex items-center gap-2 rounded-md border border-dashed border-border bg-background px-4 py-3 text-[15px] text-muted-foreground">
            <CircleDot aria-hidden="true" className="size-4" />
            No reviews are running right now.
          </p>
        ) : (
          <ul className="grid gap-2.5">
            {reviews.map((review) => (
              <li
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background p-3"
                key={review.id}
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-foreground">
                    {review.repositoryName}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <GitBranch aria-hidden="true" className="size-3.5" />
                      {review.branch}
                    </span>
                    <span>Started {formatRelativeTime(review.startedAt)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={review.status} />
                  <Button asChild size="sm" variant="secondary">
                    <Link href={review.href}>Open review</Link>
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
