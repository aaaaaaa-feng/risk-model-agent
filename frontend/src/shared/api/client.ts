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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeErrorPayload(payload: unknown): { code?: string; message?: string } {
  if (!isRecord(payload)) return {};
  const source = isRecord(payload.error) ? payload.error : payload.detail;
  if (typeof source === "string") return { message: source };
  if (Array.isArray(source)) return { code: "VALIDATION_ERROR" };
  if (!isRecord(source)) return {};
  return {
    code: typeof source.code === "string" ? source.code : undefined,
    message: typeof source.message === "string" ? source.message : undefined,
  };
}

async function responseError(response: Response): Promise<ApiError> {
  let body: unknown = {};
  try {
    body = await response.json();
  } catch {
    /* response may not be JSON */
  }
  const detail = normalizeErrorPayload(body);
  return new ApiError(
    response.status,
    detail.code || `HTTP_${response.status}`,
    detail.message || "",
  );
}

function sessionHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  if (localSessionToken) headers.set("x-risk-agent-session", localSessionToken);
  return headers;
}

function downloadFilename(response: Response): string | undefined {
  const disposition = response.headers.get("content-disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const raw = encoded || plain;
  if (!raw) return undefined;
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    /* keep the server-provided fallback name */
  }
  return decoded
    .split(/[\\/]/)
    .at(-1)
    ?.replace(/[<>:"|?*]/g, "_")
    .split("")
    .map((character) => (character.charCodeAt(0) < 32 ? "_" : character))
    .join("");
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
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_UNREACHABLE", "");
  }
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

export interface DownloadedFile {
  blob: Blob;
  filename?: string;
  contentType: string;
}

async function download(path: string): Promise<DownloadedFile> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      credentials: "same-origin",
      cache: "no-store",
      headers: sessionHeaders(),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_UNREACHABLE", "");
  }
  if (!response.ok) throw await responseError(response);
  return {
    blob: await response.blob(),
    filename: downloadFilename(response),
    contentType: response.headers.get("content-type") || "application/octet-stream",
  };
}

export async function initializeLocalSession(): Promise<void> {
  // 页面加载时先建立本机会话，后续写操作才能被后端的本地边界校验放行。
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}/session`, {
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK_UNREACHABLE", "");
  }
  if (!response.ok) throw new ApiError(response.status, `LOCAL_SESSION_${response.status}`, "");
  const body = (await response.json()) as { request_token?: string };
  if (!body.request_token) throw new ApiError(500, "LOCAL_SESSION_TOKEN_MISSING", "");
  localSessionToken = body.request_token;
}

export const httpClient = {
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
  download,
};

export function eventUrl(path: string): string {
  return `${API_ROOT}${path}`;
}
