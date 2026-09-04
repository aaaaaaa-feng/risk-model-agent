import { useCallback, useEffect, useRef, useState } from "react";
import { runsApi } from "../api/runsApi";
import { useRunEvents } from "./useRunEvents";
import { errorMessage, isAbort } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import { isCurrentRunResponse, isTerminalRunStatus, mergeEventsForRun } from "./runState";
import type { RunStreamEnd } from "./useRunEvents";
import type { Decision, Run, RunEvent } from "../types";

export const RUN_EVENT_REFRESH_DEBOUNCE_MS = 75;
export const TERMINAL_RECONCILE_MAX_ATTEMPTS = 4;

interface ReconcileScheduler {
  set: (callback: () => void, delay: number) => unknown;
  clear: (timer: unknown) => void;
}

export function terminalReconcileDelay(completedAttempts: number): number {
  return Math.min(1_000 * 2 ** Math.max(0, completedAttempts - 1), 5_000);
}

/**
 * stream_end 后做有界最终对账。终态帧会先更新本地状态；这里负责把 Run 详情、
 * 决策和项目产物最终同步回来，瞬时 HTTP 失败不会造成永久停在旧状态。
 */
export function startTerminalReconciliation(
  loadDetail: () => Promise<boolean>,
  loadRun: () => Promise<boolean>,
  onSettled: (succeeded: boolean) => void,
  scheduler: ReconcileScheduler = {
    set: (callback, delay) => window.setTimeout(callback, delay),
    clear: (timer) => window.clearTimeout(timer as number),
  },
): () => void {
  let active = true;
  let attempts = 0;
  let timer: unknown;

  const reconcile = async () => {
    if (!active) return;
    attempts += 1;
    const results = await Promise.allSettled([loadDetail(), loadRun()]);
    if (!active) return;
    const succeeded = results.every((result) => result.status === "fulfilled" && result.value);
    if (succeeded || attempts >= TERMINAL_RECONCILE_MAX_ATTEMPTS) {
      active = false;
      onSettled(succeeded);
      return;
    }
    timer = scheduler.set(() => void reconcile(), terminalReconcileDelay(attempts));
  };

  void reconcile();
  return () => {
    active = false;
    if (timer !== undefined) scheduler.clear(timer);
  };
}

const PROJECT_REFRESH_EVENT_STATUSES = new Set([
  "completed",
  "awaiting_decision",
  "approved",
  "succeeded",
  "failed",
  "blocked",
]);

export function runEventNeedsProjectRefresh(status: string): boolean {
  return PROJECT_REFRESH_EVENT_STATUSES.has(status);
}

export function activeRunEventStreamId(runId: string | null, run: Run | null): string | null {
  return run?.id === runId && isTerminalRunStatus(run.status) ? null : runId;
}

export function useRunData(
  runId: string | null,
  selectedId: string | null,
  runRef: React.MutableRefObject<string | null>,
  selectedRef: React.MutableRefObject<string | null>,
  loadDetail: () => Promise<boolean>,
) {
  const [run, setRun] = useState<Run | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const runAbort = useRef<AbortController | null>(null);
  const runRequest = useRef(0);
  const eventRefreshTimer = useRef(0);
  const eventNeedsProjectRefresh = useRef(false);
  const stopTerminalReconciliation = useRef<(() => void) | null>(null);

  const loadRunState = useCallback(
    async (includeEvents: boolean) => {
      const projectId = selectedId;
      const expectedRunId = runId;
      const requestId = ++runRequest.current;
      runAbort.current?.abort();
      if (!expectedRunId) {
        setRun(null);
        setDecision(null);
        if (includeEvents) setEvents([]);
        return true;
      }
      const controller = new AbortController();
      runAbort.current = controller;
      try {
        const runValue = await runsApi.detail(expectedRunId, controller.signal);
        const eventValue = includeEvents
          ? await runsApi.events(expectedRunId, controller.signal)
          : null;
        if (
          requestId !== runRequest.current ||
          !isCurrentRunResponse(
            projectId,
            expectedRunId,
            selectedRef.current,
            runRef.current,
            runValue.run.project_id,
          )
        )
          return false;
        setRun(runValue.run);
        setDecision(runValue.pending_decisions?.[0] || null);
        if (eventValue)
          setEvents((current) => mergeEventsForRun(current, eventValue.events, expectedRunId));
        return true;
      } catch (error) {
        if (!isAbort(error)) notify(errorMessage(error), true);
        return false;
      }
    },
    [selectedId, runId, selectedRef, runRef],
  );

  const loadRun = useCallback(() => loadRunState(true), [loadRunState]);
  const loadRunSummary = useCallback(() => loadRunState(false), [loadRunState]);

  const scheduleEventRefresh = useCallback(
    (refreshProject: boolean) => {
      eventNeedsProjectRefresh.current ||= refreshProject;
      window.clearTimeout(eventRefreshTimer.current);
      eventRefreshTimer.current = window.setTimeout(() => {
        eventRefreshTimer.current = 0;
        const shouldRefreshProject = eventNeedsProjectRefresh.current;
        eventNeedsProjectRefresh.current = false;
        void loadRunSummary();
        if (shouldRefreshProject) void loadDetail();
      }, RUN_EVENT_REFRESH_DEBOUNCE_MS);
    },
    [loadDetail, loadRunSummary],
  );

  const streamRunId = activeRunEventStreamId(runId, run);
  const streamStatus = useRunEvents(
    streamRunId,
    (event) => {
      if (event.run_id !== runRef.current) return;
      setEvents((current) => mergeEventsForRun(current, [event], event.run_id));
      // SSE 增量维护事件列表；仅把事件当作相关资源失效信号，不再定时全量刷新。
      scheduleEventRefresh(runEventNeedsProjectRefresh(event.status));
    },
    (event: RunStreamEnd) => {
      if (event.run_id !== runRef.current) return;
      window.clearTimeout(eventRefreshTimer.current);
      eventNeedsProjectRefresh.current = false;
      setRun((current) =>
        current?.id === event.run_id
          ? { ...current, status: event.status, seq: Math.max(current.seq, event.sequence) }
          : current,
      );
      stopTerminalReconciliation.current?.();
      stopTerminalReconciliation.current = startTerminalReconciliation(
        loadDetail,
        loadRunSummary,
        (succeeded) => {
          stopTerminalReconciliation.current = null;
          if (!succeeded)
            notify(
              "运行已结束，但最终状态暂时无法完整同步。请点击重载，或稍后重新选择该 Run。",
              true,
            );
        },
      );
    },
  );

  useEffect(
    () => () => {
      window.clearTimeout(eventRefreshTimer.current);
      eventNeedsProjectRefresh.current = false;
      stopTerminalReconciliation.current?.();
      stopTerminalReconciliation.current = null;
    },
    [runId],
  );

  const clearRun = useCallback(() => {
    runAbort.current?.abort();
    window.clearTimeout(eventRefreshTimer.current);
    eventNeedsProjectRefresh.current = false;
    stopTerminalReconciliation.current?.();
    stopTerminalReconciliation.current = null;
    setRun(null);
    setDecision(null);
    setEvents([]);
  }, []);

  return {
    run,
    setRun,
    decision,
    setDecision,
    events,
    setEvents,
    loadRun,
    runAbort,
    runRequest,
    streamStatus,
    clearRun,
  };
}
