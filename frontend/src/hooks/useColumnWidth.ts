import { useCallback, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

interface Options {
  storageKey: string;
  min: number;
  max: number;
  /** 无本地存储时的初始宽度 */
  initial: number;
  /** true 表示向左拖动增大宽度（用于右侧栏） */
  invert?: boolean;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function readStored(key: string): number | null {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

/**
 * 栏宽拖拽 hook：Pointer Events 实现拖拽，方向键微调（16px/格），
 * 双击分隔条恢复初始宽度；拖拽结束或键盘调整后写入 localStorage。
 */
export function useColumnWidth({ storageKey, min, max, initial, invert = false }: Options) {
  const [width, setWidth] = useState(() => clamp(readStored(storageKey) ?? initial, min, max));
  const startRef = useRef<{ x: number; width: number } | null>(null);

  const persist = useCallback(
    (value: number) => {
      try {
        localStorage.setItem(storageKey, String(value));
      } catch {
        // 忽略持久化失败，内存态仍然生效
      }
    },
    [storageKey],
  );

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      startRef.current = { x: event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
      document.body.classList.add("col-resizing");
    },
    [width],
  );

  const onPointerMove = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const start = startRef.current;
      if (!start) return;
      const delta = (event.clientX - start.x) * (invert ? -1 : 1);
      setWidth(clamp(start.width + delta, min, max));
    },
    [invert, min, max],
  );

  const endDrag = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (!startRef.current) return;
      startRef.current = null;
      document.body.classList.remove("col-resizing");
      event.currentTarget.releasePointerCapture(event.pointerId);
      setWidth((current) => {
        persist(current);
        return current;
      });
    },
    [persist],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      const step = 16;
      let delta = 0;
      if (event.key === "ArrowLeft") delta = invert ? step : -step;
      if (event.key === "ArrowRight") delta = invert ? -step : step;
      if (!delta) return;
      event.preventDefault();
      setWidth((current) => {
        const next = clamp(current + delta, min, max);
        persist(next);
        return next;
      });
    },
    [invert, min, max, persist],
  );

  const onDoubleClick = useCallback(() => {
    setWidth(initial);
    persist(initial);
  }, [initial, persist]);

  return {
    width,
    resizerProps: {
      role: "separator" as const,
      "aria-orientation": "vertical" as const,
      tabIndex: 0,
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onKeyDown,
      onDoubleClick,
    },
  };
}
