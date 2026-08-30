export interface ModelVersion {
  id: string;
  name: string;
}

export interface ScoreJob {
  id: string;
  rows: number;
}

export interface ReportData {
  schema_version: string;
  project: { name: string };
  target: { column: string };
  executive_summary?: Record<string, unknown>;
  champion?: Record<string, unknown>;
  sample_overview?: Record<string, unknown>;
  model_comparison?: Array<Record<string, unknown>>;
  feature_selection?: { selected?: Array<Record<string, unknown>> };
}
