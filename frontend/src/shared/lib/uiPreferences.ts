const COOKIE_PREFIX = "rma_ui_";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function cookieName(key: string): string {
  return `${COOKIE_PREFIX}${encodeURIComponent(key)}`;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const item = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  if (!item) return null;
  try {
    return decodeURIComponent(item.slice(prefix.length));
  } catch {
    return null;
  }
}

function writeCookie(key: string, value: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${cookieName(key)}=${encodeURIComponent(value)}; Max-Age=${COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Strict`;
}

/**
 * 读取不含密钥或业务数据的界面偏好。
 *
 * Cookie 以 host 为边界、不会因本地服务随机端口变化而丢失；localStorage
 * 只作为旧版兼容副本。读取到旧值时会自动迁移到 Cookie。
 */
export function readUiPreference(key: string): string | null {
  const cookieValue = readCookie(cookieName(key));
  if (cookieValue !== null) return cookieValue;
  if (typeof localStorage === "undefined") return null;
  try {
    const legacyValue = localStorage.getItem(key);
    if (legacyValue !== null) writeCookie(key, legacyValue);
    return legacyValue;
  } catch {
    return null;
  }
}

export function writeUiPreference(key: string, value: string): void {
  writeCookie(key, value);
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(key, value);
  } catch {
    // Cookie 已是跨端口权威副本；受限浏览器里允许仅内存态继续工作。
  }
}

export function removeUiPreference(key: string): void {
  if (typeof document !== "undefined") {
    document.cookie = `${cookieName(key)}=; Max-Age=0; Path=/; SameSite=Strict`;
  }
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.removeItem(key);
  } catch {
    // 忽略旧副本清理失败。
  }
}
