import { useCallback, useRef, useState } from "react";
import { api } from "../api";
import { errorMessage, isAbort } from "../lib/format";
import type { ProjectDetail } from "../types";

export function useProjectData(
  selectedId: string | null,
  selectedRef: React.MutableRefObject<string | null>,
  setRunId: React.Dispatch<React.SetStateAction<string | null>>,
  notify: (message: string, error?: boolean) => void,
) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const detailRequest = useRef(0);

  const loadDetail = useCallback(async () => {
    const projectId = selectedId;
    const requestId = ++detailRequest.current;
    detailAbort.current?.abort();
    if (!projectId) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    detailAbort.current = controller;
    try {
      const value = await api.get<ProjectDetail>(`/projects/${projectId}`, {
        signal: controller.signal,
      });
      if (requestId !== detailRequest.current || selectedRef.current !== projectId) return;
      setDetail(value);
      setRunId((current) => {
        if (current && value.runs.some((item) => item.id === current)) return current;
        const active = value.runs.find((item) =>
          ["awaiting_decision", "running", "queued"].includes(item.status),
        );
        return active?.id || value.runs[0]?.id || null;
      });
    } catch (error) {
      if (!isAbort(error)) notify(errorMessage(error), true);
    }
  }, [selectedId, notify, selectedRef, setRunId]);

  const clearDetail = useCallback(() => {
    detailAbort.current?.abort();
    setDetail(null);
  }, []);

  return { detail, setDetail, loadDetail, detailAbort, detailRequest, clearDetail };
}
