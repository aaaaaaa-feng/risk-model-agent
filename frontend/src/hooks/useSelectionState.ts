import { useEffect, useRef, useState } from "react";
import type { Project } from "../types";

export type View = "workbench" | "report" | "history";

export function useSelectionState(projects: Project[]) {
  const [selectedId, setSelectedId] = useState<string | null>(
    () => localStorage.getItem("risk-agent-project"),
  );
  const [runId, setRunId] = useState<string | null>(null);
  const [view, setView] = useState<View>("workbench");
  const [dataMode, setDataMode] = useState(false);
  const selectedRef = useRef<string | null>(selectedId);
  const runRef = useRef<string | null>(runId);

  useEffect(() => {
    setSelectedId((current) => {
      if (current && projects.some((p) => p.id === current)) return current;
      return projects[0]?.id || null;
    });
  }, [projects]);

  useEffect(() => {
    selectedRef.current = selectedId;
    if (selectedId) {
      localStorage.setItem("risk-agent-project", selectedId);
      setDataMode(false);
    }
  }, [selectedId]);

  useEffect(() => {
    runRef.current = runId;
  }, [runId]);

  const selectProject = (id: string) => {
    selectedRef.current = id;
    runRef.current = null;
    setSelectedId(id);
    setRunId(null);
    setView("workbench");
  };

  const selectRun = (id: string) => {
    runRef.current = id;
    setRunId(id);
    setDataMode(false);
    setView("workbench");
  };

  return {
    selectedId,
    setSelectedId,
    runId,
    setRunId,
    view,
    setView,
    dataMode,
    setDataMode,
    selectedRef,
    runRef,
    selectProject,
    selectRun,
  };
}
