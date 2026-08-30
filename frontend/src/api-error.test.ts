import { afterEach, describe, expect, it, vi } from "vitest";
import { httpClient, ApiError } from "@/shared/api/client";

afterEach(() => vi.unstubAllGlobals());

describe("API 错误边界", () => {
  it("将 FastAPI 校验数组收敛为稳定错误，不生成 [object Object]", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: [{ loc: ["body", "name"], msg: "required" }] }), {
          status: 422,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const error = await httpClient.get("/projects").catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "VALIDATION_ERROR", message: "" });
  });

  it("将 fetch 连接失败收敛为可翻译的稳定错误码", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await httpClient.get("/health").catch((value: unknown) => value);
    expect(error).toMatchObject({ status: 0, code: "NETWORK_UNREACHABLE", message: "" });
  });

  it("下载先验证响应，成功后才返回 Blob 和安全文件名", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("report", {
          status: 200,
          headers: {
            "content-type": "text/html",
            "content-disposition": "attachment; filename=../risk-report.html",
          },
        }),
      ),
    );

    const file = await httpClient.download("/reports/run-1/html");
    expect(file.filename).toBe("risk-report.html");
    expect(file.contentType).toBe("text/html");
    expect(await file.blob.text()).toBe("report");
  });
});
