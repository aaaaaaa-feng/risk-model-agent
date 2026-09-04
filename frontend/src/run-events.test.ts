import { describe, expect, it, vi } from "vitest";
import {
  parseRunEvent,
  parseRunStreamEnd,
  RUN_EVENT_MAX_RETRIES,
  RUN_EVENT_RECOVERY_PROBE_INTERVAL,
  runEventFailureAction,
  runEventRetryDelay,
  runEventStreamUrl,
} from "@/features/runs/model/useRunEvents";
import {
  activeRunEventStreamId,
  runEventNeedsProjectRefresh,
  startTerminalReconciliation,
  TERMINAL_RECONCILE_MAX_ATTEMPTS,
} from "@/features/runs/model/useRunData";
import type { Run } from "@/features/runs";

const validEvent = {
  id: "event-1",
  run_id: "run-1",
  sequence: 1,
  stage: "training",
  node: "train",
  agent: "local_worker",
  status: "running",
  summary: "正在训练候选模型",
  time: "2026-08-24T00:00:00Z",
  evidence: {},
};

describe("Run 事件流边界", () => {
  it("解析合法事件", () => {
    expect(parseRunEvent(JSON.stringify(validEvent))).toEqual(validEvent);
  });

  it("忽略损坏 JSON 和缺少必填字段的事件", () => {
    expect(parseRunEvent("{not-json")).toBeNull();
    expect(parseRunEvent(JSON.stringify({ ...validEvent, run_id: undefined }))).toBeNull();
  });

  it("只接受当前 Run 的合法终态帧", () => {
    expect(
      parseRunStreamEnd(JSON.stringify({ run_id: "run-1", status: "succeeded", sequence: 12 })),
    ).toEqual({ run_id: "run-1", status: "succeeded", sequence: 12 });
    expect(
      parseRunStreamEnd(JSON.stringify({ run_id: "run-1", status: "running", sequence: 12 })),
    ).toBeNull();
    expect(
      parseRunStreamEnd(JSON.stringify({ run_id: "run-1", status: "failed", sequence: -1 })),
    ).toBeNull();
  });

  it("实时流使用有上限的指数退避", () => {
    expect(RUN_EVENT_MAX_RETRIES).toBe(5);
    expect([1, 2, 3, 4, 5].map(runEventRetryDelay)).toEqual([3_000, 6_000, 12_000, 24_000, 30_000]);
  });

  it("五次快速重试耗尽后才进入轮询兜底，并低频探测 SSE 恢复", () => {
    expect([1, 2, 3, 4, 5].map((count) => runEventFailureAction(count).mode)).toEqual([
      "retry",
      "retry",
      "retry",
      "retry",
      "retry",
    ]);
    expect(runEventFailureAction(6)).toEqual({
      mode: "fallback",
      delay: RUN_EVENT_RECOVERY_PROBE_INTERVAL,
    });
    expect(runEventFailureAction(1, true)).toEqual({
      mode: "fallback",
      delay: RUN_EVENT_RECOVERY_PROBE_INTERVAL,
    });
  });

  it("每条事件只刷新 Run 摘要，产物或状态边界才连带刷新项目详情", () => {
    expect(runEventNeedsProjectRefresh("running")).toBe(false);
    expect(runEventNeedsProjectRefresh("queued")).toBe(false);
    expect(runEventNeedsProjectRefresh("completed")).toBe(true);
    expect(runEventNeedsProjectRefresh("awaiting_decision")).toBe(true);
    expect(runEventNeedsProjectRefresh("succeeded")).toBe(true);
  });

  it("重连地址携带最后事件序号，避免历史事件把重试计数清零", () => {
    expect(runEventStreamUrl("run_中文", 42)).toBe(
      "/api/v1/runs/run_%E4%B8%AD%E6%96%87/events/stream?after=42",
    );
  });

  it("轮询已确认终态后关闭 SSE 探测，旧 Run 终态不影响新选择", () => {
    const terminal = { id: "run-1", status: "succeeded" } as Run;
    expect(activeRunEventStreamId("run-1", terminal)).toBeNull();
    expect(activeRunEventStreamId("run-2", terminal)).toBe("run-2");
    expect(activeRunEventStreamId("run-1", { ...terminal, status: "running" })).toBe("run-1");
  });
});

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("Run 终态对账状态机", () => {
  it("瞬时失败后按有界退避重试，任一资源未成功都不会提前结束", async () => {
    const scheduled: Array<() => void> = [];
    const scheduler = {
      set: vi.fn((callback: () => void) => {
        scheduled.push(callback);
        return scheduled.length;
      }),
      clear: vi.fn(),
    };
    const detail = vi.fn().mockResolvedValue(true);
    const run = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    const settled = vi.fn();

    startTerminalReconciliation(detail, run, settled, scheduler);
    await flushPromises();
    expect(scheduler.set).toHaveBeenCalledWith(expect.any(Function), 1_000);
    expect(settled).not.toHaveBeenCalled();

    scheduled.shift()?.();
    await flushPromises();
    expect(detail).toHaveBeenCalledTimes(2);
    expect(run).toHaveBeenCalledTimes(2);
    expect(settled).toHaveBeenCalledWith(true);
  });

  it("持续失败最多尝试固定次数，不形成终态无限重试", async () => {
    const scheduled: Array<() => void> = [];
    const scheduler = {
      set: vi.fn((callback: () => void) => {
        scheduled.push(callback);
        return scheduled.length;
      }),
      clear: vi.fn(),
    };
    const load = vi.fn().mockResolvedValue(false);
    const settled = vi.fn();

    startTerminalReconciliation(load, load, settled, scheduler);
    await flushPromises();
    for (let index = 1; index < TERMINAL_RECONCILE_MAX_ATTEMPTS; index += 1) {
      scheduled.shift()?.();
      await flushPromises();
    }

    expect(load).toHaveBeenCalledTimes(TERMINAL_RECONCILE_MAX_ATTEMPTS * 2);
    expect(settled).toHaveBeenCalledOnce();
    expect(settled).toHaveBeenCalledWith(false);
    expect(scheduled).toHaveLength(0);
  });
});
