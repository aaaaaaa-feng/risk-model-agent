import { useEffect, useRef } from "react";
import { runsApi } from "../api/runsApi";
import { notify } from "@/shared/lib/notify";
import type { RunEvent } from "../types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export const RUN_EVENT_MAX_RETRIES = 5;

export function runEventRetryDelay(attempt: number): number {
  return Math.min(3_000 * 2 ** Math.max(0, attempt - 1), 30_000);
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

export function useRunEvents(
  runId: string | null,
  onEvent: (event: RunEvent) => void,
  onEnd: () => void,
) {
  const callbacks = useRef({ onEvent, onEnd });
  callbacks.current = { onEvent, onEnd };
  useEffect(() => {
    if (!runId) return;
    const activeRunId = runId;
    let source: EventSource | null = null;
    let retryTimer = 0;
    let disposed = false;
    let terminal = false;
    let retryCount = 0;
    let lastSequence = 0;

    function reconnect(current: EventSource, code: string) {
      if (disposed || terminal || source !== current) return;
      current.close();
      source = null;
      callbacks.current.onEnd();
      retryCount += 1;
      if (retryCount > RUN_EVENT_MAX_RETRIES) {
        notify({ code: "RUN_EVENT_STREAM_STOPPED" }, true);
        return;
      }
      notify({ code }, true);
      // 立即普通刷新一次，再按指数退避做有界重连。
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, runEventRetryDelay(retryCount));
    }

    function connect() {
      if (disposed || terminal) return;
      const current = new EventSource(runEventStreamUrl(activeRunId, lastSequence));
      source = current;
      current.addEventListener("run_event", (message) => {
        const event = parseRunEvent((message as MessageEvent).data);
        if (!event) {
          reconnect(current, "RUN_EVENT_STREAM_INVALID");
          return;
        }
        if (event.sequence <= lastSequence) return;
        lastSequence = event.sequence;
        retryCount = 0;
        callbacks.current.onEvent(event);
      });
      current.addEventListener("stream_end", () => {
        if (source !== current) return;
        terminal = true;
        callbacks.current.onEnd();
        current.close();
        source = null;
      });
      current.onerror = () => reconnect(current, "RUN_EVENT_STREAM_INTERRUPTED");
    }

    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      source?.close();
    };
  }, [runId]);
}
