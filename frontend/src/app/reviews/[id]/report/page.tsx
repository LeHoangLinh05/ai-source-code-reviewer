"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SeverityBreakdownChart } from "@/components/dashboard/severity-breakdown-chart";
import { CategoryDistributionChart } from "@/components/reviews/category-distribution-chart";
import { SeverityBadge } from "@/components/reviews/review-badges";
import { ReviewWorkspaceTabs } from "@/components/reviews/review-workspace-tabs";
import { ScoreTrack } from "@/components/reviews/score-track";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TechnicalDetails } from "@/components/ui/technical-details";
import { getApiErrorMessage } from "@/lib/api-error";
import { getReport, getReportIssues } from "@/lib/reports";
import type { IssueCategory, IssueSeverity, ReviewIssue } from "@/types/issue";
import type { ReviewReport, TopRiskyFile } from "@/types/report";

const SCORE_WEIGHTS: Record<IssueSeverity, number> = {
  critical: 3,
  high: 2,
  medium: 1,
  low: 0.3,
  info: 0.1,
};
const SCORE_DECAY_FACTOR = 10;

const AI_REPORT_MODEL = "langchain-react-agent-v1";
const REPORT_ISSUES_PAGE_SIZE = 100;

type DisplayScores = {
  maintainability_score: number | null;
  overall_score: number | null;
  performance_score: number | null;
  security_score: number | null;
};

export default function ReviewReportPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<ReviewIssue[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [report, setReport] = useState<ReviewReport | null>(null);

  const loadReport = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [reportData, issueData] = await Promise.all([
        getReport(jobId),
        getAllReportIssues(jobId),
      ]);
      setReport(reportData);
      setIssues(issueData);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, "Unable to load report."));
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void loadReport();
  }, [loadReport]);

  const severityData = useMemo(() => {
    if (!report) {
      return [];
    }

    return buildSeverityData(report);
  }, [report]);
  const categoryData = useMemo(() => buildCategoryData(issues), [issues]);
  const displayScores = useMemo(
    () => (report ? buildDisplayScores(report, issues) : null),
    [issues, report],
  );

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href={`/reviews/${jobId}`}>
            <ArrowLeft aria-hidden="true" />
            Review Job
          </Link>
        </Button>
      </div>

      <ReviewWorkspaceTabs activeTab="report" jobId={jobId} />

      {isLoading && !report ? <ReportSkeleton /> : null}

      {!isLoading && error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {report && displayScores ? (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <ScoreCard
              description="All confirmed findings"
              findingCount={report.total_issues}
              label="Overall"
              value={displayScores.overall_score}
            />
            <ScoreCard
              description="Security category"
              findingCount={getCategoryCount(categoryData, "security")}
              label="Security"
              value={displayScores.security_score}
            />
            <ScoreCard
              description="Bugs, style, and maintainability"
              findingCount={
                getCategoryCount(categoryData, "bug") +
                getCategoryCount(categoryData, "maintainability") +
                getCategoryCount(categoryData, "style")
              }
              label="Maintainability"
              value={displayScores.maintainability_score}
            />
            <ScoreCard
              description="Performance category"
              findingCount={getCategoryCount(categoryData, "performance")}
              label="Performance"
              value={displayScores.performance_score}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
            <Card>
              <CardHeader>
                <CardTitle>Severity Mix</CardTitle>
                <CardDescription>
                  Distribution across {report.total_issues} confirmed findings.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <SeverityBreakdownChart items={severityData} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Category Distribution</CardTitle>
                <CardDescription>
                  Where the current issue groups are concentrated.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <CategoryDistributionChart items={categoryData} />
              </CardContent>
            </Card>
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <Card>
              <CardHeader>
                <CardTitle>Executive Summary</CardTitle>
                <CardDescription>
                  Review result generated from the analysis pipeline.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-[15px] leading-6 text-muted-foreground">
                  {report.executive_summary ?? "No summary available."}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Top Risky Files</CardTitle>
                <CardDescription>
                  Files with the highest weighted issue density.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <TopRiskyFiles files={report.top_risky_files ?? []} jobId={jobId} />
              </CardContent>
            </Card>
          </section>

          <Card>
            <CardHeader>
              <CardTitle>Analysis Details</CardTitle>
              <CardDescription>
                Files analyzed and findings persisted for this report.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Metric label="Files analyzed" value={report.total_files_analyzed} />
              <Metric label="Total findings" value={report.total_issues} />
            </CardContent>
          </Card>

          <TechnicalDetails
            description="Model and report generation metadata."
            title="Report generation details"
          >
            <div className="grid gap-4">
              {report.ai_model_used !== AI_REPORT_MODEL ? (
                <div className="flex gap-3 rounded-md border border-border bg-background p-4 text-[15px] leading-6 text-muted-foreground">
                  <AlertTriangle
                    aria-hidden="true"
                    className="mt-0.5 size-5 shrink-0 text-foreground"
                  />
                  <p>
                    Report generated by{" "}
                    <code className="font-mono">
                      {report.ai_model_used ?? "an unknown pipeline"}
                    </code>
                    .
                  </p>
                </div>
              ) : null}
              <Metric
                label="Report pipeline"
                value={report.ai_model_used ?? "Not available"}
              />
            </div>
          </TechnicalDetails>
        </>
      ) : null}
    </>
  );
}

function ScoreCard({
  description,
  findingCount,
  label,
  value,
}: {
  description: string;
  findingCount: number;
  label: string;
  value: number | null;
}) {
  const status = getScoreStatus(value, findingCount);

  return (
    <Card>
      <CardContent className="grid min-h-[150px] gap-4 p-5">
        <ScoreTrack
          caption={status}
          label={label}
          showNoFindings={findingCount === 0}
          value={value}
        />
        <p className="text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      </CardContent>
    </Card>
  );
}

function TopRiskyFiles({
  files,
  jobId,
}: {
  files: TopRiskyFile[];
  jobId: string;
}) {
  if (files.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No risky files available.
      </p>
    );
  }

  return (
    <div className="grid gap-3">
      {files.map((file, index) => (
        <Link
          className="grid gap-2 rounded-md border border-border bg-background p-3 transition-colors hover:bg-muted/35"
          href={`/reviews/${jobId}/issues?file_path=${encodeURIComponent(file.path)}`}
          key={file.path}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="min-w-0 break-all text-sm font-semibold">
              <span className="mr-2 text-muted-foreground">#{index + 1}</span>
              {file.path}
            </p>
            <span className="shrink-0 rounded-md border border-border px-2 py-1 text-xs font-semibold">
              {file.issue_count}
            </span>
          </div>
          <p className="text-xs capitalize text-muted-foreground">
            Max severity
          </p>
          <SeverityBadge severity={file.max_severity} />
        </Link>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-border bg-background p-4">
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      <p className="mt-2 break-all text-xl font-semibold tracking-normal">
        {value}
      </p>
    </div>
  );
}

function ReportSkeleton() {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <Card key={index}>
          <CardContent className="p-5">
            <div className="h-28 animate-pulse rounded bg-muted" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

async function getAllReportIssues(jobId: string) {
  const firstPage = await getReportIssues(jobId, {
    page: 1,
    per_page: REPORT_ISSUES_PAGE_SIZE,
    sort: "-created_at",
  });
  const totalPages = Math.ceil(firstPage.total / REPORT_ISSUES_PAGE_SIZE);
  if (totalPages <= 1) {
    return firstPage.issues;
  }

  const remainingPages = await Promise.all(
    Array.from({ length: totalPages - 1 }, (_, index) =>
      getReportIssues(jobId, {
        page: index + 2,
        per_page: REPORT_ISSUES_PAGE_SIZE,
        sort: "-created_at",
      }),
    ),
  );
  return [
    ...firstPage.issues,
    ...remainingPages.flatMap((page) => page.issues),
  ];
}

function buildSeverityData(report: ReviewReport) {
  return [
    { key: "critical", value: report.critical_count },
    { key: "high", value: report.high_count },
    { key: "medium", value: report.medium_count },
    { key: "low", value: report.low_count },
    { key: "info", value: report.info_count },
  ] satisfies Array<{ key: IssueSeverity; value: number }>;
}

function buildCategoryData(issues: ReviewIssue[]) {
  return [
    { label: "security", value: countCategory(issues, "security") },
    { label: "performance", value: countCategory(issues, "performance") },
    {
      label: "maintainability",
      value: countCategory(issues, "maintainability"),
    },
    { label: "requirement", value: countCategory(issues, "requirement") },
    { label: "style", value: countCategory(issues, "style") },
    { label: "bug", value: countCategory(issues, "bug") },
  ] satisfies Array<{ label: IssueCategory; value: number }>;
}

function countCategory(issues: ReviewIssue[], category: IssueCategory) {
  return issues
    .filter((issue) => issue.category === category)
    .reduce((sum, issue) => sum + issue.occurrence_count, 0);
}

function getCategoryCount(
  data: Array<{ label: IssueCategory; value: number }>,
  category: IssueCategory,
) {
  return data.find((item) => item.label === category)?.value ?? 0;
}

function buildDisplayScores(
  report: ReviewReport,
  issues: ReviewIssue[],
): DisplayScores {
  return {
    maintainability_score:
      report.maintainability_score ??
      calculateIssueScore(issues, (issue) =>
        ["bug", "maintainability", "style"].includes(issue.category),
      ),
    overall_score:
      report.overall_score ?? calculateIssueScore(issues, () => true),
    performance_score:
      report.performance_score ??
      calculateIssueScore(issues, (issue) => issue.category === "performance"),
    security_score:
      report.security_score ??
      calculateIssueScore(issues, (issue) => issue.category === "security"),
  };
}

function calculateIssueScore(
  issues: ReviewIssue[],
  shouldIncludeIssue: (issue: ReviewIssue) => boolean,
) {
  const penalty = issues
    .filter(shouldIncludeIssue)
    .reduce(
      (sum, issue) =>
        sum + SCORE_WEIGHTS[issue.severity] * Math.max(issue.occurrence_count, 1),
      0,
    );

  return Number((10 * Math.exp(-penalty / SCORE_DECAY_FACTOR)).toFixed(1));
}

function getScoreStatus(value: number | null, findingCount: number) {
  if (value === null) {
    return "Not scored";
  }
  if (findingCount === 0) {
    return "No findings";
  }
  if (value < 5) {
    return "Needs attention";
  }
  if (value < 8) {
    return "Watch closely";
  }

  return "Healthy";
}
