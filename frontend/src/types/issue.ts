export type IssueSeverity = "critical" | "high" | "medium" | "low" | "info";

export type IssueCategory =
  | "security"
  | "performance"
  | "maintainability"
  | "style"
  | "bug"
  | "requirement";

export type IssueSource =
  | "ai_review"
  | "ruff"
  | "bandit"
  | "eslint"
  | "KB"
  | "secret_scanner";

export type IssueOccurrence = {
  issue_id: string;
  raw_issue_ids: string[];
  sources: IssueSource[];
  file_path: string;
  line_start: number;
  line_end: number;
  title: string;
  description: string;
  suggestion: string | null;
  confidence: number | null;
  raw_output: Record<string, unknown> | null;
  created_at: string;
};

export type ReviewIssue = {
  id: string;
  job_id: string;
  file_path: string;
  line_start: number;
  line_end: number;
  severity: IssueSeverity;
  category: IssueCategory;
  title: string;
  description: string;
  suggestion: string | null;
  source: IssueSource;
  confidence: number | null;
  raw_output: Record<string, unknown> | null;
  created_at: string;
  group_key: string | null;
  occurrence_count: number;
  raw_issue_count: number;
  affected_files: string[];
  fix_issue_ids: string[];
  primary_issue_id: string | null;
  occurrences: IssueOccurrence[];
};

export type IssueFilters = {
  category: IssueCategory | null;
  file_path: string | null;
  severity: IssueSeverity | null;
  source: IssueSource | null;
};

export type IssueSort =
  | "severity"
  | "file_path"
  | "-file_path"
  | "-created_at"
  | "created_at";

export type IssueListResponse = {
  total: number;
  page: number;
  per_page: number;
  sort: IssueSort;
  filters: IssueFilters;
  issues: ReviewIssue[];
};
