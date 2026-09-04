import { describe, expect, it, vi } from "vitest";
import {
  MAX_GLOBAL_POLLING_INTERVAL,
  MIN_GLOBAL_POLLING_INTERVAL,
  normalizeGlobalPollingInterval,
  startRunFallbackPolling,
} from "@/app/model/useGlobalPolling";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
}

describe("SSE 失败后的兜底轮询", () => {
  it("限制轮询频率，避免错误配置造成高频全量请求", () => {
    expect(normalizeGlobalPollingInterval(10)).toBe(MIN_GLOBAL_POLLING_INTERVAL);
    expect(normalizeGlobalPollingInterval(5_000)).toBe(5_000);
    expect(normalizeGlobalPollingInterval(90_000)).toBe(MAX_GLOBAL_POLLING_INTERVAL);
    expect(normalizeGlobalPollingInterval(Number.NaN)).toBe(5_000);
  });

  it("进入 fallback 时立即同步，两个请求结束前不会安排下一轮", async () => {
    const detail = deferred();
    const run = deferred();
    const scheduled: Array<() => void> = [];
    const scheduler = {
      set: vi.fn((callback: () => void) => {
        scheduled.push(callback);
        return scheduled.length;
      }),
      clear: vi.fn(),
    };
    const loadDetail = vi.fn(() => detail.promise);
    const loadRun = vi.fn(() => run.promise);

    const stop = startRunFallbackPolling(loadDetail, loadRun, 5_000, scheduler);

    expect(loadDetail).toHaveBeenCalledTimes(1);
    expect(loadRun).toHaveBeenCalledTimes(1);
    expect(scheduler.set).not.toHaveBeenCalled();

    detail.resolve();
    await flushPromises();
    expect(scheduler.set).not.toHaveBeenCalled();
    run.resolve();
    await flushPromises();
    expect(scheduler.set).toHaveBeenCalledOnce();
    expect(scheduler.set).toHaveBeenCalledWith(expect.any(Function), 5_000);

    stop();
    expect(scheduler.clear).toHaveBeenCalledWith(1);
  });

  it("SSE 恢复触发 stop 后，尚未结束的轮询不再续排", async () => {
    const detail = deferred();
    const run = deferred();
    const scheduler = { set: vi.fn(() => 1), clear: vi.fn() };
    const stop = startRunFallbackPolling(
      () => detail.promise,
      () => run.promise,
      5_000,
      scheduler,
    );

    stop();
    detail.resolve();
    run.resolve();
    await flushPromises();

    expect(scheduler.set).not.toHaveBeenCalled();
  });
});
