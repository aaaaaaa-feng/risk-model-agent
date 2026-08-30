import { httpClient } from "@/shared/api/client";
import type { RunCreatedResponse } from "@/features/runs";

export interface JoinRecommendation {
  recommendations?: Array<{ left_keys: string[]; right_keys: string[] }>;
}

export interface NotebookDocumentResponse {
  notebook: unknown;
  document: unknown;
}

export interface NotebookExecuteResponse {
  execution: {
    status: string;
    execution_count?: number;
    outputs?: Array<{
      text?: string;
      ename?: string;
      evalue?: string;
      data?: Record<string, unknown>;
    }>;
  };
}

export const dataApi = {
  uploadAsset: (projectId: string, form: FormData) =>
    httpClient.upload(`/projects/${encodeURIComponent(projectId)}/data-assets`, form),
  selectSheet: (assetId: string, sheet: string) =>
    httpClient.put(`/data-assets/${encodeURIComponent(assetId)}/sheet`, { sheet }),
  materialize: (assetId: string) =>
    httpClient.post(`/data-assets/${encodeURIComponent(assetId)}/materialize`),
  recommendJoin: (leftAssetId: string, rightAssetId: string) =>
    httpClient.get<JoinRecommendation>(
      `/join-plans/recommend?left_asset_id=${encodeURIComponent(leftAssetId)}&right_asset_id=${encodeURIComponent(rightAssetId)}`,
    ),
  createJoinPlan: (payload: Record<string, unknown>) =>
    httpClient.post<{ join_plan: { id: string } }>("/join-plans", payload),
  executeJoinPlan: (joinPlanId: string) =>
    httpClient.post(`/join-plans/${encodeURIComponent(joinPlanId)}/execute`, {
      target_columns: [],
      customer_key: null,
    }),
  createNotebook: (payload: Record<string, unknown>) =>
    httpClient.post<NotebookDocumentResponse>("/notebooks", payload),
  saveNotebook: (notebookId: string, notebook: unknown) =>
    httpClient.put(`/notebooks/${encodeURIComponent(notebookId)}`, { notebook }),
  executeNotebookCell: (notebookId: string, cellIndex: number) =>
    httpClient.post<NotebookExecuteResponse>(
      `/notebooks/${encodeURIComponent(notebookId)}/execute-cell`,
      { cell_index: cellIndex },
    ),
  importNotebookOutput: (notebookId: string, payload: Record<string, unknown>) =>
    httpClient.post(`/notebooks/${encodeURIComponent(notebookId)}/dataset-versions`, payload),
  createTargets: (payload: Record<string, unknown>) => httpClient.post("/target-tasks", payload),
  createRun: (payload: Record<string, unknown>) =>
    httpClient.post<RunCreatedResponse>("/runs", payload),
};
