import { useCallback, useRef, useState } from "react";
import { api } from "../api";
import { useRunEvents } from "./useRunEvents";
import { errorMessage, isAbort } from "../lib/format";
import { isCurrentSelection, mergeEventsForRun } from "../runState";
import type { Decision, EventsResponse, Run, RunEvent, RunResponse } from "../types";

export function useRunData(
  runId: string | null,
  selectedId: string | null,
  runRef: React.MutableRefObject<string | null>,
  selectedRef: React.MutableRefObject<string | null>,
  loadDetail: () => Promise<void>,
  notify: (message: string, error?: boolean) => void,
) {
  const [run, setRun] = useState<Run | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const runAbort = useRef<AbortController | null>(null);
  const runRequest = useRef(0);

  const loadRun = useCallback(async () => {
    const projectId = selectedId;
    const expectedRunId = runId;
    const requestId = ++runRequest.current;
    runAbort.current?.abort();
    if (!expectedRunId) {
      setRun(null);
      setDecision(null);
      setEvents([]);
      return;
    }
    const controller = new AbortController();
    runAbort.current = controller;
    try {
      const [runValue, eventValue] = await Promise.all([
        api.get<RunResponse>(`/runs/${expectedRunId}`, { signal: controller.signal }),
        api.get<EventsResponse>(`/runs/${expectedRunId}/events`, { signal: controller.signal }),
      ]);
      if (
        requestId !== runRequest.current ||
        !isCurrentSelection(projectId, expectedRunId, selectedRef.current, runRef.current)
      )
        return;
      setRun(runValue.run);
      setDecision(runValue.pending_decisions?.[0] || null);
      setEvents(mergeEventsForRun([], eventValue.events, expectedRunId));
    } catch (error) {
      if (!isAbort(error)) notify(errorMessage(error), true);
    }
  }, [selectedId, runId, notify, selectedRef, runRef]);

  useRunEvents(
    runId,
    (event) => {
      if (event.run_id !== runRef.current) return;
      setEvents((current) => mergeEventsForRun(current, [event], event.run_id));
      if (
        ["awaiting_decision", "approved", "succeeded", "failed", "blocked"].includes(event.status)
      ) {
        loadRun();
        loadDetail();
      }
    },
    () => {
      loadRun();
      loadDetail();
    },
  );

  const clearRun = useCallback(() => {
    runAbort.current?.abort();
    setRun(null);
    setDecision(null);
    setEvents([]);
  }, []);

  return { run, setRun, decision, setDecision, events, setEvents, loadRun, runAbort, runRequest, clearRun };
}
