export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface Project {
  id: string;
  name: string;
  description: string;
  status: string;
  mode: "semi_trusted" | "fully_trusted";
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
}

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
    columns_detail?: Array<{ name: string; type: string; id_candidate?: boolean; time_candidate?: boolean }>;
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

export interface Run {
  id: string;
  project_id: string;
  target_task_id: string;
  status: string;
  stage: string;
  node: string;
  mode: string;
  seq: number;
  progress: number;
  error?: string | null;
  state?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface Decision {
  id: string;
  run_id: string;
  stage: string;
  kind: string;
  status: string;
  payload: Record<string, any>;
  review?: Record<string, any>;
}

export interface RunEvent {
  id: string;
  run_id: string;
  sequence: number;
  stage: string;
  node: string;
  agent: string;
  tool?: string | null;
  status: string;
  summary: string;
  time: string;
  evidence: Record<string, any>;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  agent?: string | null;
  content: string;
  summary: string;
  created_at: string;
}

export interface ProjectDetail {
  project: Project;
  assets: DataAsset[];
  dataset_versions: DatasetVersion[];
  target_tasks: TargetTask[];
  runs: Run[];
}

export interface Settings {
  provider: string;
  api_format: "openai" | "anthropic";
  base_url: string;
  model: string;
  reviewer_model: string;
  llm_enabled: boolean;
  mode: string;
  run_token_budget: number;
  monthly_token_budget: number;
  proxy: string;
  ca_cert: string;
  notebook_network: boolean;
  telemetry: boolean;
  auto_update: boolean;
  memory_budget_mb: number;
  max_parallel_models: number;
  default_models: string[];
  api_key: string;
  api_key_configured: boolean;
  secret_storage: string;
  data_dir: string;
  synced_path_warning: boolean;
  active_profile_id?: string;
  profiles?: ProviderProfile[];
}

export interface ProviderProfile {
  id: string;
  label: string;
  provider: string;
  api_format: "openai" | "anthropic";
  base_url: string;
  model: string;
  reviewer_model: string;
  llm_enabled: boolean;
  api_key: string;
  api_key_configured: boolean;
  secret_storage: string;
  active?: boolean;
}

export interface WorkspaceStatus {
  schema_version: string;
  configured: boolean;
  needs_setup: boolean;
  source: string;
  path: string;
  current_path: string;
  projects_path: string;
  marker_present: boolean;
  synced_path_warning: boolean;
  project_count: number;
  active_run_count: number;
  pointer_path: string;
  project_storage: string;
}
