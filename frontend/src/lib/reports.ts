import { api } from "@/lib/api";
import type {
  IssueCategory,
  IssueListResponse,
  IssueSeverity,
  IssueSort,
  IssueSource,
  ReviewIssue,
} from "@/types/issue";
import type { ReviewReport, ReportSummary } from "@/types/report";

export type IssueQuery = {
  category?: IssueCategory | null;
  file_path?: string | null;
  page: number;
  per_page: number;
  severity?: IssueSeverity | null;
  sort: IssueSort;
  source?: IssueSource | null;
};

export async function getReport(jobId: string) {
  const response = await api.get<ReviewReport>(`/reports/${jobId}`);
  return response.data;
}

export async function getReportSummary(jobId: string) {
  const response = await api.get<ReportSummary>(`/reports/${jobId}/summary`);
  return response.data;
}

export async function getReportIssues(jobId: string, query: IssueQuery) {
  const response = await api.get<IssueListResponse>(`/reports/${jobId}/issues`, {
    params: query,
  });
  return response.data;
}

export async function getReportIssue(jobId: string, issueId: string) {
  const response = await api.get<ReviewIssue>(
    `/reports/${jobId}/issues/${issueId}`,
  );
  return response.data;
}
