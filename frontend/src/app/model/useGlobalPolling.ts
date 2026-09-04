import { useEffect } from "react";

export const MIN_GLOBAL_POLLING_INTERVAL = 1_000;
export const MAX_GLOBAL_POLLING_INTERVAL = 30_000;

export interface GlobalPollingOptions {
  enabled: boolean;
  interval?: number;
}

interface PollingScheduler {
  set: (callback: () => void, delay: number) => unknown;
  clear: (timer: unknown) => void;
}

export function normalizeGlobalPollingInterval(interval = 5_000): number {
  if (!Number.isFinite(interval)) return 5_000;
  return Math.min(
    MAX_GLOBAL_POLLING_INTERVAL,
    Math.max(MIN_GLOBAL_POLLING_INTERVAL, Math.round(interval)),
  );
}

/**
 * 启动一个不会重叠执行的兜底轮询。
 *
 * 下一轮只会在本轮两个请求都结束后再排队，因此慢请求不会积压。返回的
 * stop 也会阻止正在执行的最后一轮继续安排定时器，供 SSE 恢复时立即收口。
 */
export function startRunFallbackPolling(
  loadDetail: () => Promise<unknown>,
  loadRun: () => Promise<unknown>,
  interval = 5_000,
  scheduler: PollingScheduler = {
    set: (callback, delay) => window.setTimeout(callback, delay),
    clear: (timer) => window.clearTimeout(timer as number),
  },
): () => void {
  const delay = normalizeGlobalPollingInterval(interval);
  let active = true;
  let timer: unknown;

  const poll = async () => {
    if (!active) return;
    try {
      await Promise.allSettled([loadDetail(), loadRun()]);
    } finally {
      if (active) timer = scheduler.set(() => void poll(), delay);
    }
  };

  // 进入 fallback 后立即对齐一次状态，不再额外等待一个轮询周期。
  void poll();
  return () => {
    active = false;
    if (timer !== undefined) scheduler.clear(timer);
  };
}

/**
 * 正常情况下 Run 由 SSE 驱动；只有 SSE 的有界快速重试耗尽后才启用轮询。
 * 数字第四参数兼容旧调用方；新调用应传入显式的 enabled 状态。
 */
export function useGlobalPolling(
  loadDetail: () => Promise<unknown>,
  loadRun: () => Promise<unknown>,
  runId: string | null,
  options: GlobalPollingOptions | number = { enabled: false },
) {
  const enabled = typeof options === "number" ? true : options.enabled;
  const interval = typeof options === "number" ? options : options.interval;

  useEffect(() => {
    if (!runId || !enabled) return;
    return startRunFallbackPolling(loadDetail, loadRun, interval);
  }, [enabled, interval, loadDetail, loadRun, runId]);
}
