"use client";

import {
  AlertTriangle,
  ArrowLeft,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Copy,
  Wrench,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";

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
import { PaginationControls } from "@/components/ui/pagination-controls";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  buildFixSelectionItem,
  clearFixSelections,
  getFixIssueIds,
  loadFixSelections,
  mergeFixSelections,
  removeFixSelections,
  saveFixSelections,
  type FixSelectionItem,
} from "@/lib/fix-selection";
import { createFixJob } from "@/lib/fix-jobs";
import {
  formatIssueSource,
  ISSUE_SOURCE_FILTERS,
} from "@/lib/issue-source";
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
const SORT_OPTIONS: IssueSort[] = ["severity", "file_path", "-file_path"];
const DRAWER_SCROLL_TOP_THRESHOLD_PX = 240;
const SNIPPET_ID_PREFIX = "issue-snippet";

function formatSortOption(option: string): string {
  switch (option) {
    case "severity":
      return "Severity (High → Low)";
    case "file_path":
      return "File path (A → Z)";
    case "-file_path":
      return "File path (Z → A)";
    default:
      return option.replaceAll("_", " ");
  }
}

export default function ReviewIssuesPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const jobId = params.id;
  const dispatch = useAppDispatch();
  const filters = useAppSelector((state) => state.filters.issues);
  const [error, setError] = useState<string | null>(null);
  const [isCreatingFix, setIsCreatingFix] = useState(false);
  const [isIssueLoading, setIsIssueLoading] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [issueList, setIssueList] = useState<IssueListResponse | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<ReviewIssue | null>(null);
  const [fixSelections, setFixSelections] = useState<FixSelectionItem[]>([]);
  const queryFilePath = searchParams.get("file_path");
  const lastQueryFilePathRef = useRef<string | null>(null);

  useEffect(() => {
    setFixSelections(loadFixSelections(jobId, window.sessionStorage));
  }, [jobId]);

  useEffect(() => {
    router.prefetch(`/reviews/${jobId}/fixes`);
  }, [jobId, router]);

  useEffect(() => {
    if (queryFilePath !== null && queryFilePath !== lastQueryFilePathRef.current) {
      lastQueryFilePathRef.current = queryFilePath;
      dispatch(setIssueFilePath(queryFilePath));
      dispatch(setIssuePage(1));
    }
  }, [dispatch, queryFilePath]);

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

  async function createFixForSelections(
    selections: FixSelectionItem[],
  ): Promise<boolean> {
    const issueIds = getFixIssueIds(selections);
    if (issueIds.length === 0) {
      return false;
    }

    setIsCreatingFix(true);

    try {
      await createFixJob(jobId, { issue_ids: issueIds });
      toast.success("Fix job created.");
      router.push(`/reviews/${jobId}/fixes`);
      return true;
    } catch (requestError) {
      toast.error(getApiErrorMessage(requestError, "Unable to create fix job."));
      return false;
    } finally {
      setIsCreatingFix(false);
    }
  }

  function updateFixSelections(
    update: (currentItems: FixSelectionItem[]) => FixSelectionItem[],
  ) {
    setFixSelections((currentItems) => {
      const updatedItems = update(currentItems);
      saveFixSelections(jobId, updatedItems, window.sessionStorage);
      return updatedItems;
    });
  }

  function toggleIssueSelection(issue: ReviewIssue) {
    updateFixSelections((currentItems) => {
      const isSelected = currentItems.some((item) => item.groupId === issue.id);
      if (isSelected) {
        return removeFixSelections(currentItems, [issue.id]);
      }

      return mergeFixSelections(currentItems, [buildIssueFixSelection(issue)]);
    });
  }

  function clearSelection() {
    clearFixSelections(jobId, window.sessionStorage);
    setFixSelections([]);
  }

  async function createSelectedFix() {
    if (await createFixForSelections(fixSelections)) {
      clearSelection();
    }
  }

  const selectedIssueIds = new Set(
    fixSelections.map((selection) => selection.groupId),
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
            formatOption={(option) => formatIssueSource(option as IssueSource)}
            options={ISSUE_SOURCE_FILTERS}
            value={filters.source ?? ""}
          />
          <SelectFilter
            allowAll={false}
            formatOption={formatSortOption}
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
        <CardHeader className="gap-3 md:flex-row md:items-start md:justify-between md:space-y-0">
          <div className="space-y-1.5">
            <CardTitle>Issue List</CardTitle>
            <CardDescription>
              {issueList
                ? `${issueList.total} issue groups found`
                : "Issue groups will appear here after analysis."}
            </CardDescription>
          </div>
          <Button
            disabled={fixSelections.length === 0 || isCreatingFix}
            onClick={() => void createSelectedFix()}
            type="button"
          >
            <Wrench aria-hidden="true" />
            {isCreatingFix ? "Creating fix..." : "Generate fix"}
            {!isCreatingFix && fixSelections.length > 0
              ? ` (${fixSelections.length})`
              : ""}
          </Button>
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
                onToggleIssue={toggleIssueSelection}
                selectedIssueIds={selectedIssueIds}
              />
              <PaginationControls
                currentPage={filters.page}
                onPageChange={(page) => dispatch(setIssuePage(page))}
                pageSize={issueList.per_page}
                totalItems={issueList.total}
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      {selectedIssue ? (
        <IssueDrawer
          isCreatingFix={isCreatingFix}
          isLoading={isIssueLoading}
          issue={selectedIssue}
          onCreateFix={(issue) =>
            void createFixForSelections([buildIssueFixSelection(issue)])
          }
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
  formatOption = (option) => option.replaceAll("_", " "),
  value,
  allowAll = true,
  allLabel = "All",
}: {
  allLabel?: string;
  allowAll?: boolean;
  formatOption?: (option: string) => string;
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
        {allowAll ? <option value="">{allLabel}</option> : null}
        {options.map((option) => (
          <option key={option} value={option}>
            {formatOption(option)}
          </option>
        ))}
      </select>
    </div>
  );
}

function IssueTable({
  issues,
  onSelectIssue,
  onToggleIssue,
  selectedIssueIds,
}: {
  issues: ReviewIssue[];
  onSelectIssue: (issue: ReviewIssue) => Promise<void>;
  onToggleIssue: (issue: ReviewIssue) => void;
  selectedIssueIds: ReadonlySet<string>;
}) {
  return (
    <div className="overflow-x-auto border-t border-border">
      <table className="w-full min-w-[940px] text-left text-[15px]">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-6 py-3 font-medium">Select</th>
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
          {issues.map((issue) => {
            const displayTitle = getIssueDisplayTitle(
              issue,
              getIssueOccurrences(issue),
            );

            return (
              <tr
                className="cursor-pointer border-t border-border transition-colors hover:bg-muted/35"
                key={issue.id}
                onClick={() => void onSelectIssue(issue)}
              >
                <td className="px-6 py-4">
                  <input
                    aria-label={`Select ${displayTitle}`}
                    checked={selectedIssueIds.has(issue.id)}
                    className="size-4 rounded border-border"
                    onChange={() => onToggleIssue(issue)}
                    onClick={(event) => event.stopPropagation()}
                    type="checkbox"
                  />
                </td>
                <td className="px-6 py-4">
                  <SeverityBadge severity={issue.severity} />
                </td>
                <td className="px-6 py-4 capitalize text-muted-foreground">
                  {issue.category}
                </td>
                <td className="px-6 py-4 text-muted-foreground">
                  {formatIssueSource(issue.source)}
                </td>
                <td className="max-w-[260px] truncate px-6 py-4">
                  {formatIssueTableLocation(issue)}
                </td>
                <td className="px-6 py-4 font-medium">{displayTitle}</td>
                <td className="px-6 py-4 text-muted-foreground">
                  {issue.occurrence_count}
                </td>
                <td className="px-6 py-4 text-muted-foreground">
                  {issue.affected_files.length}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function IssueDrawer({
  isCreatingFix,
  isLoading,
  issue,
  onCreateFix,
  onClose,
}: {
  isCreatingFix: boolean;
  isLoading: boolean;
  issue: ReviewIssue;
  onCreateFix: (issue: ReviewIssue) => void;
  onClose: () => void;
}) {
  const locationSummary = getIssueLocationSummary(issue);
  const occurrences = getIssueOccurrences(issue);
  const displayTitle = getIssueDisplayTitle(issue, occurrences);
  const descriptionLines = getIssueDescriptionLines(issue, occurrences);
  const suggestionText = getIssueSuggestionText(issue, occurrences);
  const affectedFiles = getDisplayAffectedFiles(issue);
  const drawerRef = useRef<HTMLElement>(null);
  const [isBackToTopVisible, setIsBackToTopVisible] = useState(false);

  function scrollToDrawerTop() {
    drawerRef.current?.scrollTo({ behavior: "smooth", top: 0 });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-background/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <aside
        aria-label="Issue details"
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-6 shadow-xl shadow-foreground/10 transition-transform duration-200 ease-out"
        onScroll={(event) =>
          setIsBackToTopVisible(
            event.currentTarget.scrollTop > DRAWER_SCROLL_TOP_THRESHOLD_PX,
          )
        }
        onClick={(event) => event.stopPropagation()}
        ref={drawerRef}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <SeverityBadge severity={issue.severity} />
            <h2 className="mt-4 text-xl font-semibold tracking-normal">
              {displayTitle}
            </h2>
            <p className="mt-2 break-all text-sm text-muted-foreground">
              {locationSummary}
            </p>
            {affectedFiles.length > 1 ? (
              <AffectedFilesNavigation
                affectedFiles={affectedFiles}
                occurrences={occurrences}
              />
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
            <DrawerMetric
              label="Source"
              value={formatIssueSource(issue.source)}
            />
            <DrawerMetric
              label="Confidence"
              value={formatConfidence(issue.confidence)}
            />
          </div>
          <DetailSection title="Description">
            <div className="grid gap-2">
              {descriptionLines.map((line, index) => (
                <p key={`${index}-${line}`}>{line}</p>
              ))}
            </div>
          </DetailSection>
          {suggestionText ? (
            <DetailSection title="Suggestion">
              {suggestionText}
            </DetailSection>
          ) : null}
          <div>
            <Button
              disabled={isCreatingFix}
              onClick={() => onCreateFix(issue)}
              type="button"
            >
              <Wrench aria-hidden="true" />
              {isCreatingFix ? "Creating fix..." : "Generate fix"}
            </Button>
          </div>
          <DetailSection title="Code Context">
            <div className="grid gap-4">
              {occurrences.map((occurrence, occurrenceIndex) => (
                <CodeSnippetViewer
                  key={occurrence.issue_id}
                  occurrence={occurrence}
                  snippetId={buildSnippetId(occurrenceIndex)}
                />
              ))}
            </div>
          </DetailSection>
        </div>
        {isBackToTopVisible ? (
          <Button
            aria-label="Back to top"
            className="fixed bottom-6 right-6 z-10 rounded-full shadow-lg"
            onClick={scrollToDrawerTop}
            size="icon"
            title="Back to top"
            type="button"
          >
            <ArrowUp aria-hidden="true" />
          </Button>
        ) : null}
      </aside>
    </div>
  );
}

function AffectedFilesNavigation({
  affectedFiles,
  occurrences,
}: {
  affectedFiles: string[];
  occurrences: IssueOccurrence[];
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [hiddenFileCount, setHiddenFileCount] = useState(0);
  const fileListRef = useRef<HTMLDivElement>(null);
  const isCollapsible = hiddenFileCount > 0;

  useLayoutEffect(() => {
    const fileList = fileListRef.current;
    if (fileList === null) {
      return;
    }

    const updateHiddenFileCount = () => {
      const fileCards = Array.from(fileList.children) as HTMLElement[];
      const firstCardTop = fileCards[0]?.offsetTop;
      const firstRowCount = fileCards.filter(
        (fileCard) => fileCard.offsetTop === firstCardTop,
      ).length;

      setHiddenFileCount(Math.max(0, affectedFiles.length - firstRowCount));
    };

    updateHiddenFileCount();
    const resizeObserver = new ResizeObserver(updateHiddenFileCount);
    resizeObserver.observe(fileList);

    return () => resizeObserver.disconnect();
  }, [affectedFiles]);

  function scrollToSnippet(filePath: string) {
    const occurrenceIndex = occurrences.findIndex(
      (occurrence) => formatIssuePath(occurrence.file_path) === filePath,
    );
    if (occurrenceIndex < 0) {
      return;
    }

    document.getElementById(buildSnippetId(occurrenceIndex))?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }

  return (
    <div className="mt-4">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Affected files
      </p>
      <div
        className={`mt-2 flex flex-wrap gap-2 ${
          isExpanded ? "" : "max-h-9 overflow-hidden"
        }`}
        ref={fileListRef}
      >
        {affectedFiles.map((filePath) => {
          const hasSnippet = occurrences.some(
            (occurrence) => formatIssuePath(occurrence.file_path) === filePath,
          );

          return (
            <button
              aria-label={`Jump to code snippet for ${filePath}`}
              className="w-fit max-w-full rounded-md border border-border bg-background px-3 py-2 text-left font-mono text-xs text-foreground transition-colors hover:border-foreground/40 hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!hasSnippet}
              key={filePath}
              onClick={() => scrollToSnippet(filePath)}
              title={
                hasSnippet
                  ? `Jump to snippet for ${filePath}`
                  : "No source snippet available"
              }
              type="button"
            >
              <span className="block truncate">{filePath}</span>
            </button>
          );
        })}
      </div>
      {isCollapsible ? (
        <Button
          className="mt-2"
          onClick={() => setIsExpanded((currentValue) => !currentValue)}
          size="sm"
          type="button"
          variant="ghost"
        >
          {isExpanded ? (
            <>
              <ChevronUp aria-hidden="true" />
              Show fewer files
            </>
          ) : (
            <>
              <ChevronDown aria-hidden="true" />
              Show {hiddenFileCount} more {hiddenFileCount === 1 ? "file" : "files"}
            </>
          )}
        </Button>
      ) : null}
    </div>
  );
}

function CodeSnippetViewer({
  occurrence,
  snippetId,
}: {
  occurrence: IssueOccurrence;
  snippetId: string;
}) {
  const lineStart = occurrence.line_start ?? 1;
  const sourceContext = getSourceContext(occurrence);

  if (sourceContext === null) {
    const displayPath = formatIssuePath(occurrence.file_path);

    return (
      <div
        className="scroll-mt-6 rounded-md border border-border bg-background p-4"
        id={snippetId}
      >
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
    <div
      className="scroll-mt-6 overflow-hidden rounded-md border border-border bg-background text-xs"
      id={snippetId}
    >
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

  // Prefer the top-level source_context (already merged by the backend).
  const topLevel = getSourceContextFromRecord(rawOutput.source_context);
  if (topLevel !== null) {
    return topLevel;
  }

  // Fall back to the probe_review source_context when present.
  const probeReview = rawOutput.probe_review;
  if (isRecord(probeReview)) {
    const probeContext = getSourceContextFromRecord(probeReview.source_context);
    if (probeContext !== null) {
      return probeContext;
    }
  }

  // Last resort: use the raw code field.
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

function getSourceContextFromRecord(
  sourceContext: unknown,
): SourceContext | null {
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

  return null;
}

function issueToOccurrence(issue: ReviewIssue): IssueOccurrence {
  return {
    issue_id: issue.id,
    raw_issue_ids: [issue.id],
    sources: [issue.source],
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

function buildIssueFixSelection(issue: ReviewIssue): FixSelectionItem {
  return buildFixSelectionItem(issue);
}

function getIssueDescriptionLines(
  issue: ReviewIssue,
  occurrences: IssueOccurrence[],
) {
  const description = getPrimaryIssueDescription(issue, occurrences);

  if (occurrences.length <= 1) {
    return [description];
  }

  const affectedFileCount = getDisplayAffectedFiles(issue).length;
  const fileLabel = `${affectedFileCount} ${
    affectedFileCount === 1 ? "file" : "files"
  }`;

  return [
    description,
    `Found in ${formatOccurrenceCount(occurrences.length)} across ${fileLabel}.`,
  ];
}

function getIssueDisplayTitle(
  issue: ReviewIssue,
  occurrences: IssueOccurrence[],
) {
  const title = issue.title.trim() || "Finding";
  const ruleLabel = getIssueRuleLabel(issue);

  if (ruleLabel === null || title.toLowerCase() !== ruleLabel.toLowerCase()) {
    return title;
  }

  const description = getPrimaryIssueDescription(issue, occurrences);
  return `${ruleLabel}: ${description}`;
}

function getPrimaryIssueDescription(
  issue: ReviewIssue,
  occurrences: IssueOccurrence[],
) {
  const descriptions = [
    issue.description,
    ...occurrences.map((item) => item.description),
  ]
    .map((description) => description.trim())
    .filter(
      (description) =>
        description.length > 0 &&
        !isGeneratedGroupDescription(issue, description),
    );

  return descriptions[0] ?? "No description was provided for this finding.";
}

function isGeneratedGroupDescription(issue: ReviewIssue, description: string) {
  const ruleLabel = getIssueRuleLabel(issue);
  if (ruleLabel === null) {
    return false;
  }

  return description
    .toLowerCase()
    .startsWith(`${ruleLabel.toLowerCase()} appears in`);
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

function getIssueRuleLabel(issue: ReviewIssue) {
  const ruleId = getIssueRuleId(issue);

  return ruleId ? `${formatIssueSource(issue.source)} ${ruleId}` : null;
}

function getIssueRuleId(issue: ReviewIssue) {
  return (
    getRawIssueRuleId(issue.raw_output) ??
    getRawIssueRuleId(issue.occurrences[0]?.raw_output ?? null)
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

function getIssueLocationSummary(issue: ReviewIssue) {
  const affectedFiles = getDisplayAffectedFiles(issue);
  const occurrenceLabel = formatOccurrenceCount(issue.occurrence_count);

  if (affectedFiles.length > 1) {
    return `${occurrenceLabel} across ${affectedFiles.length} files`;
  }

  const filePath = affectedFiles[0] ?? formatIssuePath(issue.file_path);
  const lineNumber = issue.line_start ? `:${issue.line_start}` : "";
  const groupedCount = issue.occurrence_count > 1 ? ` · ${occurrenceLabel}` : "";

  return `${filePath}${lineNumber}${groupedCount}`;
}

function buildSnippetId(occurrenceIndex: number) {
  return `${SNIPPET_ID_PREFIX}-${occurrenceIndex}`;
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
          className="grid grid-cols-1 gap-3 border-b border-border px-6 py-4 md:grid-cols-[0.35fr_0.6fr_0.8fr_0.6fr_1fr_1.2fr_0.5fr_0.5fr]"
          key={index}
        >
          <div className="h-5 w-5 animate-pulse rounded bg-muted" />
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
