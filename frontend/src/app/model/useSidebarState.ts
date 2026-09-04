import { useCallback, useEffect, useState } from "react";
import { readUiPreference, writeUiPreference } from "@/shared/lib/uiPreferences";

const STORAGE_KEY = "risk-agent-sidebar";
const NARROW_QUERY = "(max-width: 1180px)";

function readStored(): boolean | null {
  const value = readUiPreference(STORAGE_KEY);
  if (value === "open") return true;
  if (value === "collapsed") return false;
  return null;
}

/**
 * 左侧栏折叠状态。
 * - 用户手动切换后写入跨端口偏好层，之后始终以用户偏好为准；
 * - 从未手动切换时，窄屏（<=1180px）默认折叠，宽屏默认展开。
 */
export function useSidebarState(): [boolean, () => void] {
  const [stored, setStored] = useState<boolean | null>(readStored);
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia(NARROW_QUERY).matches,
  );

  useEffect(() => {
    const media = window.matchMedia(NARROW_QUERY);
    const onChange = (event: MediaQueryListEvent) => setNarrow(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const toggle = useCallback(() => {
    setStored((current) => {
      const open = current ?? !window.matchMedia(NARROW_QUERY).matches;
      const next = !open;
      writeUiPreference(STORAGE_KEY, next ? "open" : "collapsed");
      return next;
    });
  }, []);

  return [stored ?? !narrow, toggle];
}
