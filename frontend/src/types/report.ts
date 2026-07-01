export type ReportScores = {
  security_score: number | null;
  maintainability_score: number | null;
  performance_score: number | null;
  overall_score: number | null;
};

export type TopRiskyFile = {
  path: string;
  issue_count: number;
  max_severity?: string;
};

export type ReviewReport = {
  id: string;
  job_id: string;
  total_files_analyzed: number;
  total_issues: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  security_score: number | null;
  maintainability_score: number | null;
  performance_score: number | null;
  overall_score: number | null;
  tech_stack: Record<string, unknown> | null;
  top_risky_files: TopRiskyFile[] | null;
  executive_summary: string | null;
  ai_model_used: string | null;
  created_at: string;
};

export type ReportSummary = {
  job_id: string;
  executive_summary: string | null;
  scores: ReportScores;
};
