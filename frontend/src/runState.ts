import type { RunEvent } from "./types";

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
