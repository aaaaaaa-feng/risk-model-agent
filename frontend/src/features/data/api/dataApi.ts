import { httpClient } from "@/shared/api/client";
import type { RunCreatedResponse } from "@/features/runs";

export interface JoinRecommendation {
  recommendations?: Array<{ left_keys: string[]; right_keys: string[] }>;
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
  createTargets: (payload: Record<string, unknown>) => httpClient.post("/target-tasks", payload),
  createRun: (payload: Record<string, unknown>) =>
    httpClient.post<RunCreatedResponse>("/runs", payload),
};
