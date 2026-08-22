import { useCallback, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../lib/format";
import type { Settings, SettingsResponse } from "../types";

export function useSettings(notify: (message: string, error?: boolean) => void) {
  const [settings, setSettings] = useState<Settings | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      const value = await api.get<SettingsResponse>("/providers/settings");
      setSettings({
        ...value.settings,
        profiles: value.profiles || value.settings?.profiles || [],
        active_profile_id: value.active_profile_id || value.settings?.active_profile_id,
      });
      return value.settings;
    } catch (error) {
      notify(errorMessage(error), true);
      return null;
    }
  }, [notify]);

  return { settings, setSettings, loadSettings };
}
