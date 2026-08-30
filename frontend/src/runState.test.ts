import { describe, expect, it } from "vitest";
import { isCurrentSelection, mergeEventsForRun } from "@/features/runs/model/runState";
import type { RunEvent } from "@/features/runs";

function event(id: string, runId: string, sequence: number): RunEvent {
  return {
    id,
    run_id: runId,
    sequence,
    stage: "training",
    node: "train",
    agent: "local_worker",
    status: "completed",
    summary: id,
    time: "2026-08-21T00:00:00Z",
    evidence: {},
  };
}

describe("run-scoped UI state", () => {
  it("never merges events from a prior run", () => {
    const current = [event("old", "run_old", 1)];
    const incoming = [event("new-2", "run_new", 2), event("new-1", "run_new", 1)];
    expect(mergeEventsForRun(current, incoming, "run_new").map((item) => item.id)).toEqual([
      "new-1",
      "new-2",
    ]);
  });

  it("rejects a response after either project or run changes", () => {
    expect(isCurrentSelection("p1", "r1", "p1", "r1")).toBe(true);
    expect(isCurrentSelection("p1", "r1", "p2", "r1")).toBe(false);
    expect(isCurrentSelection("p1", "r1", "p1", "r2")).toBe(false);
  });
});
