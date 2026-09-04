import { invoke } from "@tauri-apps/api/core";

import "./splash.css";

type BackendPhase = "starting" | "ready" | "failed" | "stopped";

interface BackendStatus {
  phase: BackendPhase;
  message: string;
  detail: string | null;
  log_path: string | null;
  backend_url: string | null;
}

const messageElement = requireElement("status-message");
const detailElement = requireElement("status-detail");
const indicatorElement = requireElement("status-indicator");
const progressElement = requireElement("progress-bar");
const actionsElement = requireElement("failure-actions");
const retryButton = requireButton("retry-button");
const logsButton = requireButton("logs-button");

let pollTimer: number | undefined;
let pollInFlight = false;

function requireElement(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`启动页缺少必需元素：${id}`);
  }
  return element;
}

function requireButton(id: string): HTMLButtonElement {
  const element = requireElement(id);
  if (!(element instanceof HTMLButtonElement)) {
    throw new Error(`启动页元素不是按钮：${id}`);
  }
  return element;
}

function renderStatus(status: BackendStatus): void {
  messageElement.textContent = status.message;
  indicatorElement.className = `status-indicator status-indicator--${status.phase}`;
  progressElement.classList.toggle("progress-track__bar--failed", status.phase === "failed");

  const hasDetail = status.phase === "failed" && Boolean(status.detail);
  detailElement.hidden = !hasDetail;
  detailElement.textContent = hasDetail ? status.detail : "";
  actionsElement.hidden = status.phase !== "failed";
  retryButton.disabled = status.phase === "starting";
  logsButton.disabled = !status.log_path;

}

function renderBridgeFailure(error: unknown): void {
  void error;
  renderStatus({
    phase: "failed",
    message: "启动状态读取失败",
    detail: "客户端无法读取本地服务状态，请重新启动客户端后再试。",
    log_path: null,
    backend_url: null,
  });
}

async function pollStatus(): Promise<void> {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const status = await invoke<BackendStatus>("backend_status");
    renderStatus(status);
  } catch (error) {
    renderBridgeFailure(error);
    stopPolling();
  } finally {
    pollInFlight = false;
  }
}

function startPolling(): void {
  stopPolling();
  void pollStatus();
  pollTimer = window.setInterval(() => void pollStatus(), 300);
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

retryButton.addEventListener("click", async () => {
  retryButton.disabled = true;
  detailElement.hidden = true;
  actionsElement.hidden = true;
  try {
    const status = await invoke<BackendStatus>("retry_backend");
    renderStatus(status);
    startPolling();
  } catch (error) {
    renderBridgeFailure(error);
  }
});

logsButton.addEventListener("click", async () => {
  logsButton.disabled = true;
  try {
    await invoke("open_log_directory");
  } catch {
    detailElement.hidden = false;
    detailElement.textContent = "无法自动打开日志目录，请重新启动客户端后再试。";
  } finally {
    logsButton.disabled = false;
  }
});

window.addEventListener("beforeunload", stopPolling);
startPolling();
