import { ApiError, eventUrl, httpClient } from "@/shared/api/client";
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
  events: async (runId: string, signal?: AbortSignal): Promise<EventsResponse> => {
    const events: EventsResponse["events"] = [];
    let after = 0;
    for (;;) {
      const page = await httpClient.get<EventsResponse>(
        `/runs/${encodeURIComponent(runId)}/events?after=${after}`,
        { signal },
      );
      events.push(...page.events);
      if (page.events.length < 5000) return { events, next_sequence: page.next_sequence };
      const next = page.next_sequence;
      if (typeof next !== "number" || !Number.isSafeInteger(next) || next <= after) {
        throw new ApiError(500, "EVENT_CURSOR_INVALID", "");
      }
      after = next;
    }
  },
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
