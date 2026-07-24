import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { SEVERITY_META } from "@/components/dashboard/severity-meta";
import { ScoreTrack } from "@/components/reviews/score-track";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { LatestReportSummary } from "@/lib/dashboard";
import { formatDateTime } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

export function LatestReportSummaryCard({
  summary,
}: {
  summary: LatestReportSummary;
}) {
  const maxSeverity = Math.max(...summary.severity.map((item) => item.value), 1);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <CardTitle>Latest report</CardTitle>
          <CardDescription className="truncate">
            {summary.repositoryName} - {formatDateTime(summary.createdAt)}
          </CardDescription>
        </div>
        <Button asChild size="sm" variant="secondary">
          <Link href={summary.href}>
            Full report
            <ArrowRight aria-hidden="true" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="grid gap-5">
        <ScoreTrack
          label="Overall score"
          showNoFindings={summary.totalIssues === 0}
          value={summary.overallScore}
        />

        <div className="grid grid-cols-3 gap-3 border-t border-border pt-4">
          <MiniScore label="Security" value={summary.securityScore} />
          <MiniScore label="Maintainability" value={summary.maintainabilityScore} />
          <MiniScore label="Performance" value={summary.performanceScore} />
        </div>

        <div className="border-t border-border pt-4">
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Severity breakdown
          </p>
          {summary.totalIssues === 0 ? (
            <p className="mt-2 text-[15px] text-muted-foreground">
              No findings in this report.
            </p>
          ) : (
            <ul className="mt-3 grid gap-2.5">
              {summary.severity.map((item) => {
                const meta = SEVERITY_META[item.key];
                return (
                  <li className="grid gap-1.5" key={item.key}>
                    <div className="flex items-center justify-between text-[13px]">
                      <span className="inline-flex items-center gap-2 text-muted-foreground">
                        <span className={cn("size-2.5 rounded-full", meta.dot)} />
                        {meta.label}
                      </span>
                      <span className="font-semibold tabular-nums text-foreground">
                        {item.value}
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded bg-muted">
                      <div
                        className={cn("h-full rounded", meta.bar)}
                        style={{ width: `${(item.value / maxSeverity) * 100}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {summary.executiveSummary ? (
          <div className="border-t border-border pt-4">
            <p className="text-xs font-medium uppercase text-muted-foreground">
              Executive summary
            </p>
            <p className="mt-2 line-clamp-3 text-[15px] leading-6 text-muted-foreground">
              {summary.executiveSummary}
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function MiniScore({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="min-w-0">
      <p className="truncate text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-xl font-extrabold tabular-nums text-foreground">
        {value === null ? "--" : value.toFixed(1)}
      </p>
    </div>
  );
}
