import { useCallback, useEffect, useState } from "react";

/*
 * 深色/浅色主题切换:
 * - 选择持久化在 localStorage(risk-agent-theme);
 * - 未手动选择过则跟随系统 prefers-color-scheme;
 * - index.html 内联脚本已先行写入 data-theme,此处 state 与之一致,不闪屏。
 */
const STORAGE_KEY = "risk-agent-theme";

export type Theme = "light" | "dark";

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* localStorage 不可用时静默回退 */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* 忽略持久化失败 */
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
