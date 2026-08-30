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

/** 在用户点击时预先打开窗口，待 HTML 安全下载后再导航到 Blob URL。 */
export function openDownloadedHtml(file: DownloadedFile, preview: Window): void {
  const url = URL.createObjectURL(file.blob);
  preview.opener = null;
  preview.location.href = url;
  // 只要主工作台仍打开，就保留预览 URL，避免报告页在固定时间后刷新失效。
  // 主页面离开时统一释放；单个 HTML 报告通常很小，这一生命周期更可预测。
  window.addEventListener("pagehide", () => URL.revokeObjectURL(url), { once: true });
}
