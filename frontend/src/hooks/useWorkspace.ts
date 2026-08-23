import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../lib/format";
import { notify } from "@/lib/notify";
import type { WorkspaceStatus } from "../types";

export function useWorkspace(onNeedsSetup?: () => void) {
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
  }, [onNeedsSetup]);

  return { workspace, setWorkspace, loadWorkspace };
}
