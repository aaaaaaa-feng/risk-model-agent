import { afterEach, describe, expect, it } from "vitest";
import {
  readUiPreference,
  removeUiPreference,
  writeUiPreference,
} from "./shared/lib/uiPreferences";

class CookieDocument {
  private values = new Map<string, string>();

  get cookie(): string {
    return [...this.values].map(([key, value]) => `${key}=${value}`).join("; ");
  }

  set cookie(serialized: string) {
    const [pair, ...attributes] = serialized.split(";").map((part) => part.trim());
    const separator = pair.indexOf("=");
    const key = pair.slice(0, separator);
    const value = pair.slice(separator + 1);
    const removes = attributes.some((part) => part.toLowerCase() === "max-age=0");
    if (removes) this.values.delete(key);
    else this.values.set(key, value);
  }
}

class MemoryStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

const originalDocument = globalThis.document;
const originalStorage = globalThis.localStorage;

afterEach(() => {
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: originalDocument,
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: originalStorage,
  });
});

function installBrowserState(): { document: CookieDocument; storage: MemoryStorage } {
  const document = new CookieDocument();
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "document", { configurable: true, value: document });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  return { document, storage };
}

describe("跨端口界面偏好", () => {
  it("优先从 host Cookie 读取且不依赖新端口的 localStorage", () => {
    const firstPort = installBrowserState();
    writeUiPreference("risk-agent-theme", "dark");
    const sharedCookie = firstPort.document.cookie;

    const secondPortStorage = new MemoryStorage();
    const secondPortDocument = new CookieDocument();
    secondPortDocument.cookie = sharedCookie;
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: secondPortDocument,
    });
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: secondPortStorage,
    });

    expect(readUiPreference("risk-agent-theme")).toBe("dark");
    expect(secondPortStorage.getItem("risk-agent-theme")).toBeNull();
  });

  it("会把旧 localStorage 值迁移到跨端口 Cookie，并可同时清理", () => {
    const browser = installBrowserState();
    browser.storage.setItem("risk-agent-project", "proj_demo");

    expect(readUiPreference("risk-agent-project")).toBe("proj_demo");
    expect(browser.document.cookie).toContain("rma_ui_risk-agent-project=proj_demo");

    removeUiPreference("risk-agent-project");
    expect(readUiPreference("risk-agent-project")).toBeNull();
    expect(browser.document.cookie).not.toContain("risk-agent-project");
  });
});
