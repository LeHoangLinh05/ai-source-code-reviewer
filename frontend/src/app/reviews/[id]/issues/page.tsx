"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  RefreshCw,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

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
];
const SOURCES: IssueSource[] = ["ai_review", "ruff", "bandit", "eslint"];
const SORT_OPTIONS: IssueSort[] = ["-created_at", "created_at", "severity", "file_path"];

export default function ReviewIssuesPage() {
  const params = useParams<{ id: string }>();
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

  async function loadIssues() {
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
  }

  useEffect(() => {
    void loadIssues();
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
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <Button asChild className="mb-4" size="sm" variant="ghost">
            <Link href={`/reviews/${jobId}`}>
              <ArrowLeft aria-hidden="true" />
              Review Job
            </Link>
          </Button>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Finding triage
          </p>
          <h1 className="mt-2 text-3xl font-extrabold tracking-normal">
            Issues
          </h1>
          <p className="mt-1 text-[15px] leading-6 text-muted-foreground">
            Filter static analyzer and AI findings by severity, source, and file.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="secondary">
            <Link href={`/reviews/${jobId}/report`}>Report</Link>
          </Button>
          <Button disabled={isLoading} onClick={() => void loadIssues()}>
            <RefreshCw aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </header>

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
              ? `${issueList.total} issues found`
              : "Seeded issues will appear here."}
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
              No issues match the current filters.
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
        className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
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
      <table className="w-full min-w-[980px] text-left text-[15px]">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-6 py-3 font-medium">Severity</th>
            <th className="px-6 py-3 font-medium">Category</th>
            <th className="px-6 py-3 font-medium">Source</th>
            <th className="px-6 py-3 font-medium">File</th>
            <th className="px-6 py-3 font-medium">Title</th>
            <th className="px-6 py-3 font-medium">Confidence</th>
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
                {issue.file_path}
              </td>
              <td className="px-6 py-4 font-medium">{issue.title}</td>
              <td className="px-6 py-4 text-muted-foreground">
                {issue.confidence ? `${Math.round(issue.confidence * 100)}%` : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: IssueSeverity }) {
  const className =
    severity === "critical"
      ? "border-slate-400 bg-background text-slate-50"
      : severity === "high"
        ? "border-slate-500 bg-background text-slate-100"
        : severity === "medium"
          ? "border-slate-600 bg-background text-slate-200"
          : severity === "low"
            ? "border-slate-700 bg-background text-slate-300"
            : "border-slate-800 bg-background text-muted-foreground";

  return (
    <span
      className={`inline-flex rounded-md border px-2 py-1 text-xs font-medium capitalize ${className}`}
    >
      {severity}
    </span>
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
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-background/80 backdrop-blur-sm">
      <aside
        aria-label="Issue details"
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-6 shadow-xl shadow-black/30 transition-transform duration-200 ease-out"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <SeverityBadge severity={issue.severity} />
            <h2 className="mt-4 text-xl font-semibold tracking-normal">
              {issue.title}
            </h2>
            <p className="mt-2 break-all text-sm text-muted-foreground">
              {issue.file_path}
              {issue.line_start ? `:${issue.line_start}` : ""}
            </p>
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
          <DetailSection title="Description">{issue.description}</DetailSection>
          {issue.suggestion ? (
            <DetailSection title="Suggestion">{issue.suggestion}</DetailSection>
          ) : null}
          <DetailSection title="Code Context">
            <CodeSnippetViewer issue={issue} />
          </DetailSection>
          {issue.suggestion ? <FixDiffBox suggestion={issue.suggestion} /> : null}
          <DetailSection title="Raw Output">
            <pre className="overflow-x-auto rounded-md border border-border bg-background p-4 font-mono text-xs text-muted-foreground">
              {JSON.stringify(issue.raw_output ?? {}, null, 2)}
            </pre>
          </DetailSection>
        </div>
      </aside>
    </div>
  );
}

function CodeSnippetViewer({ issue }: { issue: ReviewIssue }) {
  const lineStart = issue.line_start ?? 1;
  const lineEnd = issue.line_end ?? lineStart;
  const lineNumbers = buildLineWindow(lineStart, lineEnd);

  return (
    <div className="overflow-hidden rounded-md border border-border bg-background font-mono text-xs">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="truncate text-muted-foreground">
          {issue.file_path}:{lineStart}
        </span>
        <Button
          aria-label="Copy issue location"
          onClick={() =>
            void navigator.clipboard.writeText(`${issue.file_path}:${lineStart}`)
          }
          size="icon"
          type="button"
          variant="ghost"
        >
          <Copy aria-hidden="true" />
        </Button>
      </div>
      <div className="overflow-x-auto">
        {lineNumbers.map((lineNumber) => {
          const isIssueLine = lineNumber >= lineStart && lineNumber <= lineEnd;

          return (
            <div
              className={`grid min-w-[640px] grid-cols-[42px_32px_1fr] border-l-4 py-1.5 pr-4 ${
                isIssueLine
                  ? "border-l-slate-300 bg-slate-800/50 text-slate-100"
                  : "border-l-transparent text-slate-300"
              }`}
              key={lineNumber}
            >
              <span className="select-none text-right text-slate-600">
                {lineNumber}
              </span>
              <span className="select-none text-center text-slate-300">
                {isIssueLine ? <AlertTriangle className="mx-auto size-3.5" /> : ""}
              </span>
              <code className="whitespace-pre">
                {isIssueLine
                  ? `// ${issue.title}`
                  : "// Source context pending read_file_chunk"}
              </code>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FixDiffBox({ suggestion }: { suggestion: string }) {
  return (
    <section>
      <h3 className="text-sm font-semibold tracking-normal">Fix Diff</h3>
      <div className="mt-2 grid overflow-hidden rounded-md border border-border font-mono text-xs">
        <div className="border-l-4 border-l-slate-600 bg-background px-4 py-3 text-slate-300">
          <span className="text-slate-400">current context</span>
        </div>
        <div className="border-l-4 border-l-slate-300 bg-slate-800/40 px-4 py-3 text-slate-100">
          <span className="mr-2 inline-flex items-center gap-1 text-slate-300">
            <Check aria-hidden="true" className="size-3" />
            proposed fix
          </span>
          {suggestion}
        </div>
      </div>
    </section>
  );
}

function buildLineWindow(lineStart: number, lineEnd: number) {
  const firstLine = Math.max(1, lineStart - 2);
  const lastLine = Math.max(lineEnd + 2, firstLine + 4);

  return Array.from(
    { length: lastLine - firstLine + 1 },
    (_, index) => firstLine + index,
  );
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
          className="grid grid-cols-1 gap-3 border-b border-border px-6 py-4 md:grid-cols-[0.6fr_0.8fr_0.6fr_1fr_1.2fr_0.5fr]"
          key={index}
        >
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          <div className="h-5 w-24 animate-pulse rounded bg-muted" />
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          <div className="h-5 w-40 animate-pulse rounded bg-muted" />
          <div className="h-5 w-56 animate-pulse rounded bg-muted" />
          <div className="h-5 w-12 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}
