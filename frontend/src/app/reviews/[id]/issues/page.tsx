"use client";

import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Copy,
  RefreshCw,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { SeverityBadge } from "@/components/reviews/review-badges";
import { ReviewWorkspaceTabs } from "@/components/reviews/review-workspace-tabs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getApiErrorMessage } from "@/lib/api-error";
import { getReportIssue, getReportIssues } from "@/lib/reports";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  resetIssueFilters,
  setIssueCategory,
  setIssueFilePath,
  setIssuePage,
  setIssueSeverity,
  setIssueSort,
  setIssueSource,
} from "@/store/slices/filterSlice";
import type {
  IssueCategory,
  IssueOccurrence,
  IssueListResponse,
  IssueSeverity,
  IssueSort,
  IssueSource,
  ReviewIssue,
} from "@/types/issue";

const SEVERITIES: IssueSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
];
const CATEGORIES: IssueCategory[] = [
  "security",
  "performance",
  "maintainability",
  "style",
  "bug",
  "requirement",
];
const SOURCES: IssueSource[] = [
  "ai_review",
  "ruff",
  "bandit",
  "eslint",
  "KB",
  "secret_scanner",
];
const SORT_OPTIONS: IssueSort[] = ["-created_at", "created_at", "severity", "file_path"];

export default function ReviewIssuesPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const appliedFilePathRef = useRef<string | null>(null);
  const jobId = params.id;
  const dispatch = useAppDispatch();
  const filters = useAppSelector((state) => state.filters.issues);
  const [error, setError] = useState<string | null>(null);
  const [isIssueLoading, setIsIssueLoading] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [issueList, setIssueList] = useState<IssueListResponse | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<ReviewIssue | null>(null);

  const totalPages = useMemo(() => {
    if (!issueList) {
      return 1;
    }

    return Math.max(1, Math.ceil(issueList.total / issueList.per_page));
  }, [issueList]);

  useEffect(() => {
    const filePath = searchParams.get("file_path") ?? "";
    if (
      filePath &&
      filePath !== filters.filePath &&
      appliedFilePathRef.current !== filePath
    ) {
      appliedFilePathRef.current = filePath;
      dispatch(setIssueFilePath(filePath));
      dispatch(setIssuePage(1));
    }
  }, [dispatch, filters.filePath, searchParams]);

  const loadIssues = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      setIssueList(
        await getReportIssues(jobId, {
          category: filters.category,
          file_path: filters.filePath.trim() || null,
          page: filters.page,
          per_page: filters.perPage,
          severity: filters.severity,
          sort: filters.sort,
          source: filters.source,
        }),
      );
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, "Unable to load issues."));
    } finally {
      setIsLoading(false);
    }
  }, [
    filters.category,
    filters.filePath,
    filters.page,
    filters.perPage,
    filters.severity,
    filters.sort,
    filters.source,
    jobId,
  ]);

  useEffect(() => {
    void loadIssues();
  }, [loadIssues]);

  async function openIssueDrawer(issue: ReviewIssue) {
    setIsIssueLoading(true);

    try {
      setSelectedIssue(await getReportIssue(jobId, issue.id));
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, "Unable to load issue."));
    } finally {
      setIsIssueLoading(false);
    }
  }

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href={`/reviews/${jobId}`}>
            <ArrowLeft aria-hidden="true" />
            Review Job
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button disabled={isLoading} onClick={() => void loadIssues()}>
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </div>

      <ReviewWorkspaceTabs activeTab="issues" jobId={jobId} />

      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>
            Severity, category, source, file path, and sort controls.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-5">
          <SelectFilter
            label="Severity"
            onChange={(value) =>
              dispatch(setIssueSeverity((value as IssueSeverity) || null))
            }
            options={SEVERITIES}
            value={filters.severity ?? ""}
          />
          <SelectFilter
            label="Category"
            onChange={(value) =>
              dispatch(setIssueCategory((value as IssueCategory) || null))
            }
            options={CATEGORIES}
            value={filters.category ?? ""}
          />
          <SelectFilter
            label="Source"
            onChange={(value) =>
              dispatch(setIssueSource((value as IssueSource) || null))
            }
            options={SOURCES}
            value={filters.source ?? ""}
          />
          <SelectFilter
            label="Sort"
            onChange={(value) => dispatch(setIssueSort(value as IssueSort))}
            options={SORT_OPTIONS}
            value={filters.sort}
          />
          <div className="grid gap-2">
            <label className="text-sm font-medium" htmlFor="file-path-search">
              File path
            </label>
            <Input
              id="file-path-search"
              onChange={(event) => dispatch(setIssueFilePath(event.target.value))}
              placeholder="auth.py"
              value={filters.filePath}
            />
          </div>
          <div className="md:col-span-5">
            <Button
              onClick={() => dispatch(resetIssueFilters())}
              variant="secondary"
            >
              Clear filters
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader>
          <CardTitle>Issue List</CardTitle>
          <CardDescription>
            {issueList
              ? `${issueList.total} issue groups found`
              : "Issue groups will appear here after analysis."}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? <IssueTableSkeleton /> : null}
          {!isLoading && error ? (
            <div className="border-t border-border p-6">
              <p className="text-sm text-destructive">{error}</p>
            </div>
          ) : null}
          {!isLoading && !error && issueList?.issues.length === 0 ? (
            <div className="border-t border-border p-6 text-sm text-muted-foreground">
              No issue groups match the current filters.
            </div>
          ) : null}
          {!isLoading && !error && issueList && issueList.issues.length > 0 ? (
            <>
              <IssueTable
                issues={issueList.issues}
                onSelectIssue={openIssueDrawer}
              />
              <Pagination
                currentPage={filters.page}
                onPageChange={(page) => dispatch(setIssuePage(page))}
                totalPages={totalPages}
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      {selectedIssue ? (
        <IssueDrawer
          isLoading={isIssueLoading}
          issue={selectedIssue}
          onClose={() => setSelectedIssue(null)}
        />
      ) : null}
    </>
  );
}

function SelectFilter({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: string[];
  value: string;
}) {
  return (
    <div className="grid gap-2">
      <label className="text-sm font-medium">{label}</label>
      <select
        className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option.replaceAll("_", " ")}
          </option>
        ))}
      </select>
    </div>
  );
}

function IssueTable({
  issues,
  onSelectIssue,
}: {
  issues: ReviewIssue[];
  onSelectIssue: (issue: ReviewIssue) => Promise<void>;
}) {
  return (
    <div className="overflow-x-auto border-t border-border">
      <table className="w-full min-w-[900px] text-left text-[15px]">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-6 py-3 font-medium">Severity</th>
            <th className="px-6 py-3 font-medium">Category</th>
            <th className="px-6 py-3 font-medium">Source</th>
            <th className="px-6 py-3 font-medium">Location</th>
            <th className="px-6 py-3 font-medium">Title</th>
            <th className="px-6 py-3 font-medium">Occurrences</th>
            <th className="px-6 py-3 font-medium">Affected files</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((issue) => (
            <tr
              className="cursor-pointer border-t border-border transition-colors hover:bg-muted/35"
              key={issue.id}
              onClick={() => void onSelectIssue(issue)}
            >
              <td className="px-6 py-4">
                <SeverityBadge severity={issue.severity} />
              </td>
              <td className="px-6 py-4 capitalize text-muted-foreground">
                {issue.category}
              </td>
              <td className="px-6 py-4 text-muted-foreground">
                {issue.source}
              </td>
              <td className="max-w-[260px] truncate px-6 py-4">
                {formatIssueTableLocation(issue)}
              </td>
              <td className="px-6 py-4 font-medium">{issue.title}</td>
              <td className="px-6 py-4 text-muted-foreground">
                {issue.occurrence_count}
              </td>
              <td className="px-6 py-4 text-muted-foreground">
                {issue.affected_files.length}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pagination({
  currentPage,
  onPageChange,
  totalPages,
}: {
  currentPage: number;
  onPageChange: (page: number) => void;
  totalPages: number;
}) {
  return (
    <div className="flex items-center justify-between border-t border-border px-6 py-4">
      <p className="text-sm text-muted-foreground">
        Page {currentPage} of {totalPages}
      </p>
      <div className="flex gap-2">
        <Button
          disabled={currentPage <= 1}
          onClick={() => onPageChange(currentPage - 1)}
          size="sm"
          variant="secondary"
        >
          <ChevronLeft aria-hidden="true" />
          Previous
        </Button>
        <Button
          disabled={currentPage >= totalPages}
          onClick={() => onPageChange(currentPage + 1)}
          size="sm"
          variant="secondary"
        >
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

function IssueDrawer({
  isLoading,
  issue,
  onClose,
}: {
  isLoading: boolean;
  issue: ReviewIssue;
  onClose: () => void;
}) {
  const locationSummary = getIssueLocationSummary(issue);
  const occurrences = getIssueOccurrences(issue);
  const suggestionText = getIssueSuggestionText(issue, occurrences);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-background/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <aside
        aria-label="Issue details"
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-6 shadow-xl shadow-foreground/10 transition-transform duration-200 ease-out"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <SeverityBadge severity={issue.severity} />
            <h2 className="mt-4 text-xl font-semibold tracking-normal">
              {issue.title}
            </h2>
            <p className="mt-2 break-all text-sm text-muted-foreground">
              {locationSummary.primary}
            </p>
            {locationSummary.secondary ? (
              <p className="mt-1 break-all text-sm text-muted-foreground">
                {locationSummary.secondary}
              </p>
            ) : null}
          </div>
          <Button
            aria-label="Close issue details"
            onClick={onClose}
            size="icon"
            variant="ghost"
          >
            <X aria-hidden="true" />
          </Button>
        </div>

        <div className="mt-6 grid gap-5">
          {isLoading ? (
            <div className="h-20 animate-pulse rounded-md bg-muted" />
          ) : null}
          <div className="grid gap-3 rounded-md border border-border bg-background p-4 sm:grid-cols-3">
            <DrawerMetric label="Category" value={issue.category} />
            <DrawerMetric label="Source" value={issue.source} />
            <DrawerMetric
              label="Confidence"
              value={formatConfidence(issue.confidence)}
            />
          </div>
          <DetailSection title="Description">
            {getIssueDescriptionText(issue, occurrences)}
          </DetailSection>
          {suggestionText ? (
            <DetailSection title="Suggestion">
              {suggestionText}
            </DetailSection>
          ) : null}
          <DetailSection title="Code Context">
            <div className="grid gap-4">
              {occurrences.map((occurrence) => (
                <CodeSnippetViewer
                  key={occurrence.issue_id}
                  occurrence={occurrence}
                />
              ))}
            </div>
          </DetailSection>
        </div>
      </aside>
    </div>
  );
}

function CodeSnippetViewer({
  occurrence,
}: {
  occurrence: IssueOccurrence;
}) {
  const lineStart = occurrence.line_start ?? 1;
  const sourceContext = getSourceContext(occurrence);

  if (sourceContext === null) {
    const displayPath = formatIssuePath(occurrence.file_path);

    return (
      <div className="rounded-md border border-border bg-background p-4">
        <p className="font-mono text-sm text-muted-foreground">
          {displayPath}:{lineStart}
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          Source context is not available for this finding yet.
        </p>
      </div>
    );
  }

  const { lines, startLine } = sourceContext;
  const lineEnd = occurrence.line_end ?? lineStart;
  const displayPath = formatIssuePath(occurrence.file_path);

  return (
    <div className="overflow-hidden rounded-md border border-border bg-background text-xs">
      <div className="flex items-center justify-between border-b border-border px-3 py-2 font-mono">
        <span className="truncate text-muted-foreground">
          {displayPath}:{lineStart}
        </span>
        <Button
          aria-label="Copy issue location"
          onClick={() =>
            void navigator.clipboard.writeText(`${displayPath}:${lineStart}`)
          }
          size="icon"
          type="button"
          variant="ghost"
        >
          <Copy aria-hidden="true" />
        </Button>
      </div>
      <div className="overflow-x-auto font-mono">
        {lines.map((line, index) => {
          const lineNumber = startLine + index;
          const isIssueLine = lineNumber >= lineStart && lineNumber <= lineEnd;

          return (
            <div
              className={`grid min-w-[640px] grid-cols-[42px_32px_1fr] border-l-4 py-1.5 pr-4 ${
                isIssueLine
                  ? "border-l-foreground bg-muted/60 text-foreground"
                  : "border-l-transparent text-muted-foreground"
              }`}
              key={lineNumber}
            >
              <span className="select-none text-right text-muted-foreground">
                {lineNumber}
              </span>
              <span className="select-none text-center text-foreground">
                {isIssueLine ? <AlertTriangle className="mx-auto size-3.5" /> : ""}
              </span>
              <code className="whitespace-pre">{line}</code>
            </div>
          );
        })}
      </div>
    </div>
  );
}

type SourceContext = {
  lines: string[];
  startLine: number;
};

function getSourceContext(occurrence: IssueOccurrence): SourceContext | null {
  const rawOutput = occurrence.raw_output;
  if (rawOutput === null) {
    return null;
  }

  const sourceContext = rawOutput.source_context;
  if (
    isRecord(sourceContext) &&
    Array.isArray(sourceContext.lines) &&
    typeof sourceContext.start_line === "number"
  ) {
    return {
      lines: sourceContext.lines.map((line) => String(line)),
      startLine: Math.max(1, sourceContext.start_line),
    };
  }

  const code =
    typeof rawOutput.code === "string"
      ? rawOutput.code
      : null;

  if (!code?.trim()) {
    return null;
  }

  return {
    lines: code.replaceAll("\r\n", "\n").split("\n"),
    startLine: occurrence.line_start ?? 1,
  };
}

function issueToOccurrence(issue: ReviewIssue): IssueOccurrence {
  return {
    issue_id: issue.id,
    file_path: issue.file_path,
    line_start: issue.line_start,
    line_end: issue.line_end,
    title: issue.title,
    description: issue.description,
    suggestion: issue.suggestion,
    confidence: issue.confidence,
    raw_output: issue.raw_output,
    created_at: issue.created_at,
  };
}

function getIssueOccurrences(issue: ReviewIssue) {
  if (issue.occurrences.length > 0) {
    return issue.occurrences;
  }

  return [issueToOccurrence(issue)];
}

function getIssueDescriptionText(
  issue: ReviewIssue,
  occurrences: IssueOccurrence[],
) {
  if (occurrences.length <= 1) {
    return issue.description;
  }

  const affectedFileCount = getDisplayAffectedFiles(issue).length;
  const fileLabel = `${affectedFileCount} ${
    affectedFileCount === 1 ? "file" : "files"
  }`;

  return `${getIssueDisplayName(issue)} appears in ${formatOccurrenceCount(
    occurrences.length,
  )} across ${fileLabel}.`;
}

function getIssueSuggestionText(
  issue: ReviewIssue,
  occurrences: IssueOccurrence[],
) {
  if (occurrences.length <= 1) {
    return issue.suggestion;
  }

  if (!occurrences.some((occurrence) => occurrence.suggestion)) {
    return null;
  }

  if (getIssueRuleId(issue) === "F401") {
    return "Remove the highlighted unused imports.";
  }

  return "Apply the relevant fix at each highlighted occurrence.";
}

function getIssueDisplayName(issue: ReviewIssue) {
  const ruleId = getIssueRuleId(issue);

  if (ruleId === "F401") {
    return "Unused imports";
  }

  return ruleId ? `${formatIssueSource(issue.source)} ${ruleId}` : issue.title;
}

function getIssueRuleId(issue: ReviewIssue) {
  return getRawIssueRuleId(issue.raw_output) ?? getRawIssueRuleId(
    issue.occurrences[0]?.raw_output ?? null,
  );
}

function getRawIssueRuleId(rawOutput: Record<string, unknown> | null) {
  if (rawOutput === null) {
    return null;
  }

  for (const key of ["code", "test_id", "ruleId"]) {
    const value = rawOutput[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

type IssueLocationSummary = {
  primary: string;
  secondary: string | null;
};

function getIssueLocationSummary(issue: ReviewIssue): IssueLocationSummary {
  const affectedFiles = getDisplayAffectedFiles(issue);
  const occurrenceLabel = formatOccurrenceCount(issue.occurrence_count);

  if (affectedFiles.length > 1) {
    return {
      primary: `${occurrenceLabel} across ${affectedFiles.length} files`,
      secondary: `Affected files: ${affectedFiles.join(", ")}`,
    };
  }

  const filePath = affectedFiles[0] ?? formatIssuePath(issue.file_path);
  const lineNumber = issue.line_start ? `:${issue.line_start}` : "";
  const groupedCount = issue.occurrence_count > 1 ? ` · ${occurrenceLabel}` : "";

  return {
    primary: `${filePath}${lineNumber}${groupedCount}`,
    secondary: null,
  };
}

function formatIssueTableLocation(issue: ReviewIssue) {
  const affectedFiles = getDisplayAffectedFiles(issue);

  if (affectedFiles.length > 1) {
    return `${affectedFiles.length} files`;
  }

  return affectedFiles[0] ?? formatIssuePath(issue.file_path);
}

function getDisplayAffectedFiles(issue: ReviewIssue) {
  const filePaths =
    issue.affected_files.length > 0
      ? issue.affected_files
      : issue.occurrences.map((occurrence) => occurrence.file_path);

  return Array.from(new Set(filePaths.map((filePath) => formatIssuePath(filePath))));
}

function formatOccurrenceCount(count: number) {
  return `${count} ${count === 1 ? "occurrence" : "occurrences"}`;
}

function formatIssueSource(source: IssueSource) {
  return source === "KB" ? source : source.replaceAll("_", " ");
}

function formatIssuePath(filePath: string | null) {
  if (!filePath) {
    return "Unknown file";
  }

  return filePath
    .replaceAll("\\", "/")
    .replace(/^.*\/sandbox\/[^/]+\//, "");
}

function DetailSection({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <section>
      <h3 className="text-sm font-semibold tracking-normal">{title}</h3>
      <div className="mt-2 text-[15px] leading-6 text-muted-foreground">
        {children}
      </div>
    </section>
  );
}

function IssueTableSkeleton() {
  return (
    <div className="border-t border-border">
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          className="grid grid-cols-1 gap-3 border-b border-border px-6 py-4 md:grid-cols-[0.6fr_0.8fr_0.6fr_1fr_1.2fr_0.5fr_0.5fr]"
          key={index}
        >
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          <div className="h-5 w-24 animate-pulse rounded bg-muted" />
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          <div className="h-5 w-40 animate-pulse rounded bg-muted" />
          <div className="h-5 w-56 animate-pulse rounded bg-muted" />
          <div className="h-5 w-16 animate-pulse rounded bg-muted" />
          <div className="h-5 w-16 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function DrawerMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 break-all text-sm font-semibold capitalize text-foreground">
        {value}
      </p>
    </div>
  );
}

function formatConfidence(confidence: number | null) {
  return confidence === null ? "Not provided" : `${Math.round(confidence * 100)}%`;
}
