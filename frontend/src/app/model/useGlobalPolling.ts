import { useEffect } from "react";

export function useGlobalPolling(
  loadDetail: () => Promise<void>,
  loadRun: () => Promise<void>,
  runId: string | null,
  interval = 5000,
) {
  useEffect(() => {
    const timer = window.setInterval(() => {
      loadDetail();
      if (runId) loadRun();
    }, interval);
    return () => window.clearInterval(timer);
  }, [loadDetail, loadRun, runId, interval]);
}
