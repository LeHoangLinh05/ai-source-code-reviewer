import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { SeverityBreakdownChart } from "@/components/dashboard/severity-breakdown-chart";
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

export function LatestReportSummaryCard({
  summary,
}: {
  summary: LatestReportSummary;
}) {
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
            <SeverityBreakdownChart items={summary.severity} />
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
