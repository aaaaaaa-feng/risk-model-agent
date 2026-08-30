import type { Json } from "@/shared/types/json";

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
