import { httpClient } from "@/shared/api/client";
import type { DownloadedFile } from "@/shared/api/client";
import type {
  BackupsResponse,
  ProviderTestResponse,
  SettingsResponse,
  WorkspaceStatus,
} from "../types";

export const settingsApi = {
  get: () => httpClient.get<SettingsResponse>("/providers/settings"),
  save: (payload: Record<string, unknown>) => httpClient.put("/providers/settings", payload),
  testProvider: (payload: Record<string, unknown>) =>
    httpClient.post<ProviderTestResponse>("/providers/test", payload),
  activateProvider: (profileId: string) =>
    httpClient.post(`/providers/profiles/${encodeURIComponent(profileId)}/activate`),
  backups: () => httpClient.get<BackupsResponse>("/backups"),
  createBackup: () => httpClient.post("/backups"),
  downloadBackup: (backupId: string): Promise<DownloadedFile> =>
    httpClient.download(`/backups/${encodeURIComponent(backupId)}/download`),
  reset: () => httpClient.post("/system/reset-settings", { confirm: true, clear_api_key: false }),
  workspace: () => httpClient.get<{ workspace: WorkspaceStatus }>("/workspace"),
  chooseWorkspaceFolder: () =>
    httpClient.post<{ path: string | null; cancelled: boolean }>("/workspace/native-picker", {}),
  selectWorkspace: (path: string) =>
    httpClient.post<{ workspace: WorkspaceStatus }>("/workspace/select", { path }),
};
