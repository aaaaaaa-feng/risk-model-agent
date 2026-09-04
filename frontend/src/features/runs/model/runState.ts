import type { RunEvent } from "../types";

const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "blocked"]);

export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return Boolean(status && TERMINAL_RUN_STATUSES.has(status));
}

export function shouldUseRunFallbackPolling(
  streamStatus: string,
  runStatus: string | null | undefined,
): boolean {
  return streamStatus === "fallback" && !isTerminalRunStatus(runStatus);
}

export function mergeEventsForRun(
  current: RunEvent[],
  incoming: RunEvent[],
  runId: string,
): RunEvent[] {
  const map = new Map(
    current.filter((item) => item.run_id === runId).map((item) => [item.id, item]),
  );
  incoming.filter((item) => item.run_id === runId).forEach((item) => map.set(item.id, item));
  return [...map.values()].sort((left, right) => left.sequence - right.sequence);
}

export function isCurrentSelection(
  expectedProjectId: string | null,
  expectedRunId: string | null,
  currentProjectId: string | null,
  currentRunId: string | null,
): boolean {
  return expectedProjectId === currentProjectId && expectedRunId === currentRunId;
}

/**
 * 除了请求代次与当前选择，还要核对响应中 Run 的真实项目归属。
 * 这样即使 URL 或旧偏好里留下了已经失效的 project/run 组合，也不会短暂串页。
 */
export function isCurrentRunResponse(
  expectedProjectId: string | null,
  expectedRunId: string | null,
  currentProjectId: string | null,
  currentRunId: string | null,
  responseProjectId: string,
): boolean {
  return (
    expectedProjectId !== null &&
    responseProjectId === expectedProjectId &&
    isCurrentSelection(expectedProjectId, expectedRunId, currentProjectId, currentRunId)
  );
}
