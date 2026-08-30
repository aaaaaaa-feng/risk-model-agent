import { useCallback, useState } from "react";
import { settingsApi } from "../api/settingsApi";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import type { WorkspaceStatus } from "../types";

export function useWorkspace(onNeedsSetup?: () => void) {
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);

  const loadWorkspace = useCallback(async () => {
    try {
      const value = await settingsApi.workspace();
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
