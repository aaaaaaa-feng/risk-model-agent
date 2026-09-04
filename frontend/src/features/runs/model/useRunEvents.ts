import { useEffect, useRef, useState } from "react";
import { runsApi } from "../api/runsApi";
import { notify } from "@/shared/lib/notify";
import type { RunEvent } from "../types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export const RUN_EVENT_MAX_RETRIES = 5;
export const RUN_EVENT_RECOVERY_PROBE_INTERVAL = 30_000;

export type RunEventStreamStatus = "idle" | "connecting" | "connected" | "fallback" | "terminal";

export interface RunEventFailureAction {
  mode: "retry" | "fallback";
  delay: number;
}

export interface RunStreamEnd {
  run_id: string;
  status: "succeeded" | "failed" | "blocked";
  sequence: number;
}

export function runEventRetryDelay(attempt: number): number {
  return Math.min(3_000 * 2 ** Math.max(0, attempt - 1), 30_000);
}

/** 初次连接加五次快速重试均失败后，转为轮询并仅低频探测 SSE 恢复。 */
export function runEventFailureAction(
  failureCount: number,
  alreadyFallback = false,
): RunEventFailureAction {
  if (alreadyFallback || failureCount > RUN_EVENT_MAX_RETRIES) {
    return { mode: "fallback", delay: RUN_EVENT_RECOVERY_PROBE_INTERVAL };
  }
  return { mode: "retry", delay: runEventRetryDelay(failureCount) };
}

export function runEventStreamUrl(runId: string, after: number): string {
  return runsApi.eventStreamUrl(runId, after);
}

/** SSE 是外部输入：解析或契约不符时返回 null，不让异常冒泡到整页。 */
export function parseRunEvent(data: unknown): RunEvent | null {
  let value: unknown = data;
  try {
    value = typeof data === "string" ? JSON.parse(data) : data;
  } catch {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.run_id !== "string" ||
    typeof value.sequence !== "number" ||
    !Number.isSafeInteger(value.sequence) ||
    value.sequence < 1 ||
    typeof value.stage !== "string" ||
    typeof value.node !== "string" ||
    typeof value.agent !== "string" ||
    typeof value.status !== "string" ||
    typeof value.summary !== "string" ||
    typeof value.time !== "string" ||
    !isRecord(value.evidence)
  )
    return null;
  return value as unknown as RunEvent;
}

/** 终态帧也按正向契约解析，避免损坏数据直接停止全部刷新。 */
export function parseRunStreamEnd(data: unknown): RunStreamEnd | null {
  let value: unknown = data;
  try {
    value = typeof data === "string" ? JSON.parse(data) : data;
  } catch {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.run_id !== "string" ||
    typeof value.status !== "string" ||
    !["succeeded", "failed", "blocked"].includes(value.status) ||
    typeof value.sequence !== "number" ||
    !Number.isSafeInteger(value.sequence) ||
    value.sequence < 0
  )
    return null;
  return value as unknown as RunStreamEnd;
}

export function useRunEvents(
  runId: string | null,
  onEvent: (event: RunEvent) => void,
  onEnd: (event: RunStreamEnd) => void,
): RunEventStreamStatus {
  const callbacks = useRef({ onEvent, onEnd });
  const [streamStatus, setStreamStatus] = useState<RunEventStreamStatus>("idle");
  callbacks.current = { onEvent, onEnd };
  useEffect(() => {
    if (!runId) {
      setStreamStatus("idle");
      return;
    }
    const activeRunId = runId;
    let source: EventSource | null = null;
    let retryTimer = 0;
    let disposed = false;
    let terminal = false;
    let fallbackMode = false;
    let fallbackNotified = false;
    let retryCount = 0;
    let lastSequence = 0;

    function publishStatus(status: RunEventStreamStatus) {
      if (!disposed) setStreamStatus(status);
    }

    function scheduleConnect(delay: number) {
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, delay);
    }

    function connectionFailed(current: EventSource | null) {
      if (disposed || terminal || (current && source !== current)) return;
      current?.close();
      source = null;
      retryCount += 1;
      const action = runEventFailureAction(retryCount, fallbackMode);
      if (action.mode === "fallback") {
        fallbackMode = true;
        publishStatus("fallback");
        if (!fallbackNotified) {
          fallbackNotified = true;
          notify({ code: "RUN_EVENT_STREAM_STOPPED" }, true);
        }
        // fallback 期间由普通刷新保证可用性，同时低频探测 SSE；恢复后上层会停轮询。
        scheduleConnect(action.delay);
        return;
      }
      publishStatus("connecting");
      scheduleConnect(action.delay);
    }

    function connected(current: EventSource, verifiedByEvent = false) {
      if (disposed || terminal || source !== current) return;
      // onopen 足以停止轮询，但只有收到合法事件才清除失败历史。这样可避免
      // “连接成功后立刻断开/返回坏数据”反复把重试计数清零而永不进入 fallback。
      if (verifiedByEvent) {
        retryCount = 0;
        fallbackMode = false;
        fallbackNotified = false;
      }
      publishStatus("connected");
    }

    function connect() {
      if (disposed || terminal) return;
      if (!fallbackMode) publishStatus("connecting");
      let current: EventSource;
      try {
        current = new EventSource(runEventStreamUrl(activeRunId, lastSequence));
      } catch {
        connectionFailed(null);
        return;
      }
      source = current;
      current.onopen = () => connected(current);
      current.addEventListener("run_event", (message) => {
        const event = parseRunEvent((message as MessageEvent).data);
        if (!event || event.run_id !== activeRunId) {
          connectionFailed(current);
          return;
        }
        if (event.sequence <= lastSequence) return;
        lastSequence = event.sequence;
        connected(current, true);
        callbacks.current.onEvent(event);
      });
      current.addEventListener("stream_end", (message) => {
        if (source !== current) return;
        const event = parseRunStreamEnd((message as MessageEvent).data);
        if (!event || event.run_id !== activeRunId) {
          connectionFailed(current);
          return;
        }
        terminal = true;
        publishStatus("terminal");
        callbacks.current.onEnd(event);
        current.close();
        source = null;
      });
      current.onerror = () => connectionFailed(current);
    }

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      source?.close();
    };
  }, [runId]);
  return streamStatus;
}
