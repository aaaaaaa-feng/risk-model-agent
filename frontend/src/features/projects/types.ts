import type { DataAsset, DatasetVersion, TargetTask } from "@/features/data";
import type { Run } from "@/features/runs";

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

export interface ProjectDetail {
  project: Project;
  assets: DataAsset[];
  dataset_versions: DatasetVersion[];
  target_tasks: TargetTask[];
  runs: Run[];
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface ProjectCreatedResponse {
  project: Project;
}
