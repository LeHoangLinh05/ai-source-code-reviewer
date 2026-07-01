export type IssueSeverity = "critical" | "high" | "medium" | "low" | "info";

export type IssueCategory =
  | "security"
  | "performance"
  | "maintainability"
  | "style"
  | "bug";

export type IssueSource = "ai_review" | "ruff" | "bandit" | "eslint";

export type ReviewIssue = {
  id: string;
  job_id: string;
  file_path: string;
  line_start: number | null;
  line_end: number | null;
  severity: IssueSeverity;
  category: IssueCategory;
  title: string;
  description: string;
  suggestion: string | null;
  source: IssueSource;
  confidence: number | null;
  raw_output: Record<string, unknown> | null;
  created_at: string;
};

export type IssueFilters = {
  category: IssueCategory | null;
  file_path: string | null;
  severity: IssueSeverity | null;
  source: IssueSource | null;
};

export type IssueSort = "-created_at" | "created_at" | "severity" | "file_path";

export type IssueListResponse = {
  total: number;
  page: number;
  per_page: number;
  sort: IssueSort;
  filters: IssueFilters;
  issues: ReviewIssue[];
};
