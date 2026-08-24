import { describe, expect, it } from "vitest";
import {
  parseRunEvent,
  RUN_EVENT_MAX_RETRIES,
  runEventRetryDelay,
  runEventStreamUrl,
} from "./hooks/useRunEvents";

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

  it("实时流使用有上限的指数退避", () => {
    expect(RUN_EVENT_MAX_RETRIES).toBe(5);
    expect([1, 2, 3, 4, 5].map(runEventRetryDelay)).toEqual([3_000, 6_000, 12_000, 24_000, 30_000]);
  });

  it("重连地址携带最后事件序号，避免历史事件把重试计数清零", () => {
    expect(runEventStreamUrl("run_中文", 42)).toBe(
      "/api/v1/runs/run_%E4%B8%AD%E6%96%87/events/stream?after=42",
    );
  });
});
