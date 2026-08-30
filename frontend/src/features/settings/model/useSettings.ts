import { useCallback, useState } from "react";
import { settingsApi } from "../api/settingsApi";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import type { Settings } from "../types";

export function useSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      const value = await settingsApi.get();
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
  }, []);

  return { settings, setSettings, loadSettings };
}
