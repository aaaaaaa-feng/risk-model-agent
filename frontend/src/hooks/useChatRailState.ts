import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "risk-agent-chat-rail";

export type ChatRailMode = "full" | "narrow" | "collapsed";

function autoMode(width: number): ChatRailMode {
  if (width <= 1180) return "collapsed";
  if (width < 1440) return "narrow";
  return "full";
}

function readStored(): ChatRailMode | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "full" || value === "narrow" || value === "collapsed") return value;
  } catch {
    // 隐私模式等场景下 localStorage 不可用，退化为自动策略
  }
  return null;
}

/**
 * 右侧 Agent 对话栏宽度状态。
 * - 三档：full 320px / narrow 260px / collapsed 0px（由 CSS 变量落到布局）；
 * - 用户手动切换后写入 localStorage；未手动切换时按视口宽度自动选择档位。
 */
export function useChatRailState(): {
  mode: ChatRailMode;
  collapsed: boolean;
  toggle: () => void;
  setMode: (mode: ChatRailMode) => void;
} {
  const [stored, setStored] = useState<ChatRailMode | null>(readStored);
  const [auto, setAuto] = useState<ChatRailMode>(() =>
    typeof window === "undefined" ? "full" : autoMode(window.innerWidth),
  );

  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setAuto(autoMode(window.innerWidth)));
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const persist = useCallback((mode: ChatRailMode) => {
    setStored(mode);
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 忽略持久化失败，内存态仍然生效
    }
  }, []);

  const mode = stored ?? auto;

  const toggle = useCallback(() => {
    persist(mode === "collapsed" ? "full" : "collapsed");
  }, [mode, persist]);

  return { mode, collapsed: mode === "collapsed", toggle, setMode: persist };
}
