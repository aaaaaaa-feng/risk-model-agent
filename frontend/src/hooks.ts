import { useEffect, useRef } from "react";
import { eventUrl } from "./api";
import type { RunEvent } from "./types";

export function useRunEvents(
  runId: string | null,
  onEvent: (event: RunEvent) => void,
  onEnd: () => void,
) {
  const callbacks = useRef({ onEvent, onEnd });
  callbacks.current = { onEvent, onEnd };
  useEffect(() => {
    if (!runId) return;
    const source = new EventSource(eventUrl(`/runs/${runId}/events/stream`));
    source.addEventListener("run_event", (message) => {
      callbacks.current.onEvent(JSON.parse((message as MessageEvent).data));
    });
    source.addEventListener("stream_end", () => {
      callbacks.current.onEnd();
      source.close();
    });
    return () => source.close();
  }, [runId]);
}
