import type { IssueSource } from "@/types/issue";

export const ISSUE_SOURCE_LABELS: Record<IssueSource, string> = {
  KB: "AI review",
  ai_review: "AI review",
  bandit: "Bandit",
  eslint: "ESLint",
  ruff: "Ruff",
  secret_scanner: "Secret scanner",
};

export const ISSUE_SOURCE_FILTERS: IssueSource[] = [
  "ai_review",
  "ruff",
  "bandit",
  "eslint",
  "secret_scanner",
];

export function formatIssueSource(source: IssueSource): string {
  return ISSUE_SOURCE_LABELS[source];
}
