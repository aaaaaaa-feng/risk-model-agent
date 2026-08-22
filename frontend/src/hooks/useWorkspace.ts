import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../lib/format";
import type { WorkspaceStatus } from "../types";

export function useWorkspace(
  notify: (message: string, error?: boolean) => void,
  onNeedsSetup?: () => void,
) {
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);

  const loadWorkspace = useCallback(async () => {
    try {
      const value = await api.get<{ workspace: WorkspaceStatus }>("/workspace");
      setWorkspace(value.workspace);
      if (value.workspace.needs_setup) onNeedsSetup?.();
      return value.workspace;
    } catch (error) {
      notify(errorMessage(error), true);
      return null;
    }
  }, [notify, onNeedsSetup]);

  return { workspace, setWorkspace, loadWorkspace };
}
