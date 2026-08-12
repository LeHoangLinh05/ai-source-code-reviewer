import { ArrowRight } from "lucide-react";
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
import type { RecentReview } from "@/lib/dashboard";
import { formatDateTime } from "@/lib/dashboard";

export function RecentActivity({ reviews }: { reviews: RecentReview[] }) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle>Recent review activity</CardTitle>
          <CardDescription>Your most recent reviews and their results.</CardDescription>
        </div>
        <Button asChild size="sm" variant="secondary">
          <Link href="/reviews">
            View all
            <ArrowRight aria-hidden="true" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {reviews.length === 0 ? (
          <p className="border-t border-border p-5 text-[15px] text-muted-foreground">
            No reviews have been created yet. Start one from a repository.
          </p>
        ) : (
          <>
            {/* Mobile: stacked list */}
            <ul className="divide-y divide-border border-t border-border md:hidden">
              {reviews.map((review) => (
                <li key={review.id}>
                  <Link
                    className="flex flex-col gap-2 p-4 transition-colors hover:bg-muted/35"
                    href={review.href}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate text-sm font-semibold text-foreground">
                        {review.repositoryName}
                      </span>
                      <StatusBadge status={review.status} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
                      <span>Branch {review.branch}</span>
                      <span>{formatFindings(review.totalIssues)}</span>
                      <span>{formatDateTime(review.date)}</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>

            {/* Desktop: table */}
            <div className="hidden overflow-x-auto border-t border-border md:block">
              <table className="w-full min-w-[640px] text-left text-[15px]">
                <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-5 py-3 font-medium">Repository</th>
                    <th className="px-5 py-3 font-medium">Status</th>
                    <th className="px-5 py-3 font-medium">Findings</th>
                    <th className="px-5 py-3 font-medium">Branch</th>
                    <th className="px-5 py-3 font-medium">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {reviews.map((review) => (
                    <tr
                      className="border-t border-border transition-colors hover:bg-muted/35"
                      key={review.id}
                    >
                      <td className="px-5 py-4">
                        <Link
                          className="font-semibold text-foreground hover:text-primary/80"
                          href={review.href}
                        >
                          {review.repositoryName}
                        </Link>
                      </td>
                      <td className="px-5 py-4">
                        <StatusBadge status={review.status} />
                      </td>
                      <td className="px-5 py-4 tabular-nums text-muted-foreground">
                        {formatFindings(review.totalIssues)}
                      </td>
                      <td className="px-5 py-4 text-muted-foreground">
                        {review.branch}
                      </td>
                      <td className="px-5 py-4 text-muted-foreground">
                        {formatDateTime(review.date)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function formatFindings(total: number | null) {
  if (total === null) {
    return "--";
  }
  return `${total} ${total === 1 ? "finding" : "findings"}`;
}
