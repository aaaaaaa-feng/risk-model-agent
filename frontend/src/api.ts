// 使用同源相对路径，确保开发页和打包页不会混用不同端口的后端。
const API_ROOT = "/api/v1";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let localSessionToken = "";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const method = (init?.method || "GET").toUpperCase();
  if (init?.body && !(init.body instanceof FormData))
    headers.set("content-type", "application/json");
  if (MUTATING_METHODS.has(method) && localSessionToken) {
    // 只有写操作需要本机临时会话令牌，避免把令牌发给只读请求。
    headers.set("x-risk-agent-session", localSessionToken);
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (!response.ok) {
    let body: {
      error?: { code?: string; message?: string };
      detail?: { code?: string; message?: string } | string;
    } = {};
    try {
      body = await response.json();
    } catch {
      /* response may not be JSON */
    }
    const detail = (typeof body.error === "object" && body.error) ||
      (typeof body.detail === "object" && body.detail) || {
        message: typeof body.detail === "string" ? body.detail : undefined,
      };
    throw new ApiError(
      response.status,
      detail.code || `HTTP_${response.status}`,
      detail.message || String(detail),
    );
  }
  return response.json() as Promise<T>;
}

export async function initializeLocalSession(): Promise<void> {
  // 页面加载时先建立本机会话，后续写操作才能被后端的本地边界校验放行。
  const response = await fetch(`${API_ROOT}/session`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`LOCAL_SESSION_${response.status}`);
  const body = (await response.json()) as { request_token?: string };
  if (!body.request_token) throw new Error("LOCAL_SESSION_TOKEN_MISSING");
  localSessionToken = body.request_token;
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>(path, init),
  post: <T>(path: string, payload?: unknown, init?: RequestInit) =>
    request<T>(path, {
      ...init,
      method: "POST",
      body: payload === undefined ? undefined : JSON.stringify(payload),
    }),
  put: <T>(path: string, payload: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(payload) }),
  patch: <T>(path: string, payload: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(payload) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

export function eventUrl(path: string): string {
  return `${API_ROOT}${path}`;
}
