const API_ROOT = "/api/v1";

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
  if (init?.body && !(init.body instanceof FormData)) headers.set("content-type", "application/json");
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (!response.ok) {
    let body: any = {};
    try { body = await response.json(); } catch { /* response may not be JSON */ }
    const detail = body.error || body.detail || {};
    throw new ApiError(response.status, detail.code || `HTTP_${response.status}`, detail.message || String(detail));
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, payload?: unknown) => request<T>(path, { method: "POST", body: payload === undefined ? undefined : JSON.stringify(payload) }),
  put: <T>(path: string, payload: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(payload) }),
  patch: <T>(path: string, payload: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(payload) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

export function eventUrl(path: string): string {
  return `${API_ROOT}${path}`;
}
