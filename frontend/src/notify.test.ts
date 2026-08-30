import { beforeEach, describe, expect, it, vi } from "vitest";

const toastSpies = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastSpies }));

import { notify } from "./lib/notify";

describe("错误提示", () => {
  beforeEach(() => vi.clearAllMocks());

  it("相同错误使用稳定 id，避免轮询反复刷屏", () => {
    notify(new Error("Failed to fetch"), true);
    notify(new Error("Failed to fetch"), true);

    expect(toastSpies.error).toHaveBeenCalledTimes(2);
    const firstOptions = toastSpies.error.mock.calls[0][1];
    const secondOptions = toastSpies.error.mock.calls[1][1];
    expect(firstOptions.id).toBe(secondOptions.id);
    expect(toastSpies.error.mock.calls[0][0]).not.toContain("Failed to fetch");
  });
});
