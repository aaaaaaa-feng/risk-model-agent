import { describe, expect, it } from "vitest";
import { alignProjectSessionResources } from "@/app/model/projectSessionState";
import type { ProjectDetail } from "@/features/projects";
import type { Decision, Run, RunEvent } from "@/features/runs";

describe("项目会话资源归属", () => {
  it("hash 切换后的首帧不会展示上一个项目和 Run 的缓存状态", () => {
    const detail = { project: { id: "project_old" } } as ProjectDetail;
    const run = { id: "run_old", project_id: "project_old" } as Run;
    const decision = { id: "decision_old", run_id: "run_old" } as Decision;
    const events = [{ id: "event_old", run_id: "run_old" }] as RunEvent[];

    expect(
      alignProjectSessionResources("project_new", "run_new", {
        detail,
        run,
        decision,
        events,
      }),
    ).toEqual({ detail: null, run: null, decision: null, events: [] });
  });

  it("只保留当前 Run 的决策与事件", () => {
    const detail = { project: { id: "project_a" }, runs: [] } as unknown as ProjectDetail;
    const run = { id: "run_a", project_id: "project_a" } as Run;
    const decision = { id: "decision_a", run_id: "run_a" } as Decision;
    const events = [
      { id: "event_a", run_id: "run_a" },
      { id: "event_old", run_id: "run_old" },
    ] as RunEvent[];

    expect(
      alignProjectSessionResources("project_a", "run_a", { detail, run, decision, events }),
    ).toEqual({ detail, run, decision, events: [events[0]] });
  });

  it("详情已验证 Run 归属时，可在独立 Run 请求返回前使用项目内摘要", () => {
    const summarizedRun = { id: "run_new", project_id: "project_new" } as Run;
    const detail = {
      project: { id: "project_new" },
      runs: [summarizedRun],
    } as unknown as ProjectDetail;
    const oldRun = { id: "run_old", project_id: "project_old" } as Run;

    expect(
      alignProjectSessionResources("project_new", "run_new", {
        detail,
        run: oldRun,
        decision: null,
        events: [],
      }).run,
    ).toBe(summarizedRun);
  });
});
