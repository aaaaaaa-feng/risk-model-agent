import { useCallback, useEffect, useState } from "react";
import { readUiPreference, writeUiPreference } from "@/shared/lib/uiPreferences";

/*
 * 深色/浅色主题切换:
 * - 选择通过跨端口界面偏好层持久化；
 * - 未手动选择过则跟随系统 prefers-color-scheme;
 * - index.html 内联脚本已先行写入 data-theme,此处 state 与之一致,不闪屏。
 */
const STORAGE_KEY = "risk-agent-theme";

export type Theme = "light" | "dark";

function initialTheme(): Theme {
  const stored = readUiPreference(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    writeUiPreference(STORAGE_KEY, theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
