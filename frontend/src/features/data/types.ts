export interface DataAsset {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  format: string;
  sheet?: string | null;
  rows?: number | null;
  columns?: number | null;
  status: string;
  metadata?: { sheets?: string[]; resource_plan?: Record<string, unknown> };
}

export interface DatasetVersion {
  id: string;
  project_id: string;
  label: string;
  rows: number;
  columns: number;
  profile?: {
    binary_candidates?: string[];
    target_candidates?: Array<{ column: string; missing: number; values: string[] }>;
    columns_detail?: Array<{
      name: string;
      type: string;
      id_candidate?: boolean;
      time_candidate?: boolean;
    }>;
  };
  lineage?: Record<string, unknown>;
  created_at: string;
}

export interface TargetTask {
  id: string;
  project_id: string;
  dataset_version_id: string;
  target_column: string;
  status: string;
  valid_sample_count: number;
  queue_position: number;
}
