import { useCallback, useEffect, useRef } from "react";

type Orientation = "horizontal" | "vertical";

interface UseRovingTabIndexOptions {
  active: boolean;
  itemCount: number;
  orientation?: Orientation;
  onActivate?: (index: number) => void;
}

export function useRovingTabIndex({
  active,
  itemCount,
  orientation = "horizontal",
  onActivate,
}: UseRovingTabIndexOptions) {
  const itemsRef = useRef<Array<HTMLElement | null>>([]);
  const selectedIndexRef = useRef(0);
  const onActivateRef = useRef(onActivate);

  useEffect(() => {
    onActivateRef.current = onActivate;
  }, [onActivate]);

  const focusItem = useCallback((index: number) => {
    selectedIndexRef.current = index;
    itemsRef.current.forEach((node, i) => {
      if (node) node.tabIndex = i === index ? 0 : -1;
    });
    itemsRef.current[index]?.focus();
  }, []);

  useEffect(() => {
    if (!active) return;
    itemsRef.current = itemsRef.current.slice(0, itemCount);
    itemsRef.current.forEach((node, i) => {
      if (node) node.tabIndex = i === selectedIndexRef.current ? 0 : -1;
    });
  }, [active, itemCount]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent, index: number) => {
      if (!active) return;

      const isHorizontal = orientation === "horizontal";
      const nextKey = isHorizontal ? "ArrowRight" : "ArrowDown";
      const prevKey = isHorizontal ? "ArrowLeft" : "ArrowUp";

      let nextIndex: number | null = null;
      if (event.key === nextKey) {
        nextIndex = (index + 1) % itemCount;
      } else if (event.key === prevKey) {
        nextIndex = (index + itemCount - 1) % itemCount;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = itemCount - 1;
      }

      if (nextIndex === null) return;
      event.preventDefault();
      focusItem(nextIndex);
      onActivateRef.current?.(nextIndex);
    },
    [active, itemCount, orientation, focusItem],
  );

  const setItemRef = useCallback(
    (index: number) => (node: HTMLElement | null) => {
      itemsRef.current[index] = node;
    },
    [],
  );

  return { setItemRef, handleKeyDown };
}
