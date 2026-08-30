import { httpClient } from "@/shared/api/client";
import type { ProjectCreatedResponse, ProjectDetail, ProjectsResponse } from "../types";

export interface CreateProjectInput {
  name: string;
  description: string;
  mode: string;
  metadata: Record<string, string>;
}

export interface CreateDemoProjectInput {
  mode: string;
  rows: number;
  seed: number;
}

export const projectsApi = {
  list: () => httpClient.get<ProjectsResponse>("/projects"),
  detail: (projectId: string, signal?: AbortSignal) =>
    httpClient.get<ProjectDetail>(`/projects/${encodeURIComponent(projectId)}`, { signal }),
  create: (payload: CreateProjectInput) =>
    httpClient.post<ProjectCreatedResponse>("/projects", payload),
  createDemo: (payload: CreateDemoProjectInput) =>
    httpClient.post<ProjectCreatedResponse>("/projects/demo", payload),
};
