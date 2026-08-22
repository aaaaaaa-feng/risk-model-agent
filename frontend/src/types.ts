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
  state?: Record<string, Json>;
  created_at: string;
  updated_at: string;
}

export type DecisionKind =
  | "confirm_target"
  | "confirm_data"
  | "confirm_split"
  | "confirm_screening"
  | "confirm_binning"
  | "confirm_models";

export interface Decision {
  id: string;
  run_id: string;
  stage: string;
  kind: DecisionKind;
  status: string;
  payload: DecisionPayload;
  review?: Review;
}

export interface Review {
  status?: string;
  issues?: ReviewIssue[];
  evidence?: Record<string, Json>;
}

export interface ReviewIssue {
  code?: string;
  message?: string;
  [key: string]: Json | undefined;
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
  evidence: Record<string, Json>;
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

export interface TargetDecisionPayload {
  title?: string;
  summary: TargetSummary;
}

export interface TargetSummary {
  valid_count?: number;
  invalid_count?: number;
  missing_count?: number;
  negative_count?: number;
  positive_count?: number;
  bad_rate?: number;
  target?: TargetSummary;
  review?: Review;
}

export interface DataDecisionPayload {
  title?: string;
  summary: DataSummary;
}

export interface DataSummary {
  issues?: DataIssue[];
  actions?: DataAction[];
  review?: Review;
}

export interface DataIssue {
  code: string;
  message: string;
  severity: string;
  [key: string]: Json | undefined;
}

export interface DataAction {
  id: string;
  kind: string;
  recommended?: boolean;
  columns?: string[];
  [key: string]: Json | undefined;
}

export interface SplitDecisionPayload {
  title?: string;
  summary: SplitSummary;
}

export interface SplitSummary {
  method?: "time_holdout" | "random_stratified";
  time_column?: string | null;
  customer_key?: string | null;
  test_size?: number;
  oot_size?: number;
  random_state?: number;
  plan?: SplitSummary;
  review?: Review;
}

export interface ScreeningDecisionPayload {
  title?: string;
  summary: ScreeningSummary;
}

export interface ScreeningSummary {
  included?: string[];
  excluded?: ScreeningExcluded[];
  thresholds?: ScreeningThresholds;
  review?: Review;
}

export interface ScreeningExcluded {
  column: string;
  reason: string;
  recoverable: boolean;
  missing_rate?: number;
  iv?: number;
}

export interface ScreeningThresholds {
  iv?: number;
  missing_rate?: number;
  correlation?: number;
}

export interface BinningDecisionPayload {
  title?: string;
  summary: BinningSummary;
}

export interface BinningSummary {
  version?: string;
  specs?: Record<string, BinSpec>;
  non_monotonic?: string[];
  review?: Review;
}

export interface BinSpec {
  kind: "numeric" | "categorical";
  edges?: number[];
  groups?: string[][];
  rare_values?: string[];
  iv?: number;
  monotonic?: boolean;
  source?: string;
  table?: BinRow[];
  merge_suggestions?: MergeSuggestion[];
}

export interface BinRow {
  bin: string;
  count: number;
  good: number;
  bad: number;
  bad_rate: number;
  woe: number;
  iv: number;
}

export interface MergeSuggestion {
  left_bin: string;
  right_bin: string;
  merged_bad_rate: number;
  distance: number;
}

export interface ModelsDecisionPayload {
  title?: string;
  summary: ModelsSummary;
}

export interface ModelsSummary {
  plan?: ModelPlan;
  review?: Review;
}

export interface ModelPlan {
  models: string[];
  score: ScoreConfig;
  search_budget: number;
}

export interface ScoreConfig {
  minimum?: number;
  maximum?: number;
  base_score?: number;
  base_odds?: number;
  pdo?: number;
}

export type DecisionPayload =
  | TargetDecisionPayload
  | DataDecisionPayload
  | SplitDecisionPayload
  | ScreeningDecisionPayload
  | BinningDecisionPayload
  | ModelsDecisionPayload;

export interface ProjectsResponse {
  projects: Project[];
}

export interface ProjectCreatedResponse {
  project: Project;
}

export interface RunResponse {
  run: Run;
  pending_decisions?: Decision[];
}

export interface EventsResponse {
  events: RunEvent[];
}

export interface RunCreatedResponse {
  run: Run;
}

export interface SettingsResponse {
  settings: Settings;
  profiles?: ProviderProfile[];
  active_profile_id?: string;
}

export interface WorkspaceResponse {
  workspace: WorkspaceStatus;
}

export interface ProviderTestResponse {
  ok: boolean;
  model?: string;
  error_code?: string;
}

export interface Backup {
  id: string;
  created_at: string;
}

export interface BackupsResponse {
  backups: Backup[];
}
