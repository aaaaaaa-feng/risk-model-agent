export { errorMessage } from "./errors";

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function formatNumber(value: unknown): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString();
}

export function formatPercent(value: unknown, digits = 2): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function formatMetric(value: unknown, digits = 4): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString();
}
