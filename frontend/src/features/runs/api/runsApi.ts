import { eventUrl, httpClient } from "@/shared/api/client";
import type { EventsResponse, RunCreatedResponse, RunResponse } from "../types";

export interface CreateRunInput {
  project_id: string;
  target_task_id: string;
  mode: string;
}

export const runsApi = {
  create: (payload: CreateRunInput) => httpClient.post<RunCreatedResponse>("/runs", payload),
  detail: (runId: string, signal?: AbortSignal) =>
    httpClient.get<RunResponse>(`/runs/${encodeURIComponent(runId)}`, { signal }),
  events: (runId: string, signal?: AbortSignal) =>
    httpClient.get<EventsResponse>(`/runs/${encodeURIComponent(runId)}/events`, { signal }),
  decide: (runId: string, decisionId: string, approved: boolean, edits: Record<string, unknown>) =>
    httpClient.post(
      `/runs/${encodeURIComponent(runId)}/decisions/${encodeURIComponent(decisionId)}`,
      {
        approved,
        edits,
      },
    ),
  eventStreamUrl: (runId: string, after: number) =>
    eventUrl(`/runs/${encodeURIComponent(runId)}/events/stream?after=${after}`),
};
