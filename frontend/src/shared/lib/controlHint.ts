import { isValidElement, type ReactNode } from "react";

function visibleText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(visibleText).join(" ");
  if (isValidElement<{ children?: ReactNode }>(node)) return visibleText(node.props.children);
  return "";
}

/**
 * 为可点击控件提供统一的鼠标悬停说明。
 * 优先使用业务明确传入的 title，否则使用 aria-label 或可见文案。
 */
export function controlHint(
  children: ReactNode,
  ariaLabel: unknown,
  fallback = "执行此操作",
): string {
  if (typeof ariaLabel === "string" && ariaLabel.trim()) return ariaLabel.trim();
  return visibleText(children).replace(/\s+/g, " ").trim() || fallback;
}
