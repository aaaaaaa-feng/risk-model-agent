import type { DownloadedFile } from "../api/client";

/** 使用 Blob 下载，避免接口失败时导航到原始 JSON 错误页。 */
export function saveDownloadedFile(file: DownloadedFile, fallbackName: string): void {
  const url = URL.createObjectURL(file.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename || fallbackName;
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

/** Reject error documents before opening the same-origin, script-free preview. */
export function validateHtmlReport(file: DownloadedFile): void {
  if (!/^text\/html(?:;|$)/i.test(file.contentType)) throw new Error("REPORT_PREVIEW_INVALID");
}
