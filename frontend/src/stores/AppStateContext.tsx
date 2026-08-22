import { createContext, useContext } from "react";
import type { Decision, Project, ProjectDetail, Run, Settings, WorkspaceStatus } from "../types";

export interface AppState {
  notify: (message: string, error?: boolean) => void;
  settings: Settings | null;
  workspace: WorkspaceStatus | null;
  projects: Project[];
  selectedId: string | null;
  detail: ProjectDetail | null;
  run: Run | null;
  decision: Decision | null;
}

export const AppStateContext = createContext<AppState | null>(null);

export function useAppState() {
  const context = useContext(AppStateContext);
  if (!context) throw new Error("useAppState must be used within AppStateContext.Provider");
  return context;
}
