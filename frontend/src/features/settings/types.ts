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

export interface SettingsResponse {
  settings: Settings;
  profiles?: ProviderProfile[];
  active_profile_id?: string;
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
