import { describe, expect, it } from "vitest";
import { validateHtmlReport } from "@/shared/lib/download";

describe("HTML 预览", () => {
  it("接受独立 HTML 报告", () => {
    expect(() =>
      validateHtmlReport({
        blob: new Blob(["<h1>报告</h1>"]),
        contentType: "text/html; charset=utf-8",
      }),
    ).not.toThrow();
  });
  it("不会把错误响应作为 HTML 预览", () => {
    expect(() =>
      validateHtmlReport({
        blob: new Blob(['{"error":"failed"}']),
        contentType: "application/json",
      }),
    ).toThrow("REPORT_PREVIEW_INVALID");
  });
});
