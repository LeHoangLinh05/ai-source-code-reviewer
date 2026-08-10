import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { SeverityBreakdownChart } from "@/components/dashboard/severity-breakdown-chart";
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
    <Card className="overflow-hidden">
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
        <div className="grid grid-cols-3 gap-3 border-t border-border pt-4">
          <MiniFinding label="Total" value={summary.totalIssues} />
          <MiniFinding label="Critical" value={severityCount(summary, "critical")} />
          <MiniFinding label="High" value={severityCount(summary, "high")} />
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

function MiniFinding({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0">
      <p className="truncate text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-xl font-extrabold tabular-nums text-foreground">
        {value}
      </p>
    </div>
  );
}

function severityCount(summary: LatestReportSummary, key: "critical" | "high") {
  return summary.severity.find((item) => item.key === key)?.value ?? 0;
}
