import { describe, expect, it } from "vitest";
import { ApiError } from "@/shared/api/client";
import { errorMessage, eventSummary, translateError } from "@/shared/lib/errors";

const containsChinese = (value: string) => /[\u3400-\u9fff]/.test(value);

describe("用户友好错误翻译", () => {
  it("将网络英文异常转为中文原因和建议", () => {
    const text = errorMessage(new TypeError("Failed to fetch"));
    expect(containsChinese(text)).toBe(true);
    expect(text).toContain("请确认应用仍在运行");
    expect(text).not.toContain("Failed to fetch");
  });

  it("不把 HTTP 码、英文异常或对象字符串当作主文案", () => {
    const httpText = errorMessage(new ApiError(503, "HTTP_503", "Internal Server Error"));
    expect(httpText).not.toMatch(/HTTP_503|Internal Server Error|\[object Object\]/);
    expect(errorMessage({})).not.toContain("[object Object]");
  });

  it("隐藏技术堆栈，只保留友好处理建议", () => {
    const text = errorMessage(
      new Error('Traceback (most recent call last): File "worker.py", line 4 TypeError: bad'),
      { context: "notebook" },
    );
    expect(text).not.toMatch(/Traceback|worker\.py|TypeError/);
    expect(text).toContain("请检查当前单元格");
  });

  it("诊断码保留在结构化结果，不进入主文案", () => {
    const result = translateError(
      { code: "PROVIDER_AUTH_FAILED", message: "Unauthorized" },
      { context: "provider" },
    );
    expect(result.code).toBe("PROVIDER_AUTH_FAILED");
    expect(result.text).not.toContain("PROVIDER_AUTH_FAILED");
    expect(result.text).toContain("API 密钥未通过验证");
  });

  it("保留后端已经友好的中文说明，不重复追加建议", () => {
    const source = "所选文件夹不可写，请换一个本机文件夹后重试。";
    expect(errorMessage({ code: "WORKSPACE_PATH_NOT_WRITABLE", message: source })).toBe(source);
  });

  it("中文说明前的技术码不进入主文案", () => {
    const text = errorMessage({
      code: "WORKSPACE_PATH_NOT_WRITABLE",
      message: "WORKSPACE_PATH_NOT_WRITABLE: 所选文件夹不可写，请换一个本机文件夹后重试。",
    });
    expect(text).not.toContain("WORKSPACE_PATH_NOT_WRITABLE");
    expect(text).toContain("所选文件夹不可写");
  });

  it("失败 Run 事件不直接展示英文异常", () => {
    const text = eventSummary("failed", "Internal Server Error", "MODEL_TRAIN_FAILED");
    expect(text).toContain("候选模型训练没有成功");
    expect(text).not.toContain("Internal Server Error");
  });

  it.each([
    "读取 /Users/feng/private/data.csv 失败",
    "读取 C:\\Users\\feng\\private\\data.csv 失败",
    "读取 \\\\server\\share\\private.csv 失败",
    "读取 /home/feng/private/data.csv 失败",
    "读取路径/Users/feng/private/data.csv失败",
    "读取C:\\Users\\feng\\private\\data.csv失败",
  ])("绝对路径不进入用户错误主文案: %s", (source) => {
    const text = errorMessage(new Error(source));
    expect(text).not.toContain(source);
    expect(text).toContain("请重试");
  });

  it.each([
    "调用失败，密钥：sk-example-secret-123456",
    "连接失败，token=example-token-value",
    "样本 13800138000 处理失败",
    "样本 11010519491231002X 处理失败",
    "联系 test.user@example.com 后重试",
  ])("敏感值不进入用户错误主文案: %s", (source) => {
    const text = errorMessage(new Error(source));
    expect(text).not.toContain(source);
    expect(text).toContain("请重试");
  });

  it("运行事件流异常明确告知已降级和恢复策略", () => {
    const interrupted = errorMessage({ code: "RUN_EVENT_STREAM_INTERRUPTED" });
    const invalid = errorMessage({ code: "RUN_EVENT_STREAM_INVALID" });
    expect(interrupted).toContain("实时连接暂时中断");
    expect(interrupted).toContain("普通刷新");
    expect(interrupted).toContain("自动重连");
    expect(invalid).toContain("运行进度无法识别");
    expect(invalid).toContain("重新读取最新运行状态");
    const stopped = errorMessage({ code: "RUN_EVENT_STREAM_STOPPED" });
    expect(stopped).toContain("普通刷新");
    expect(stopped).toContain("低频尝试恢复实时连接");
  });

  it("对话事件损坏时明确告知已恢复已保存回复", () => {
    const text = errorMessage({ code: "CONVERSATION_EVENT_STREAM_INVALID" });
    expect(text).toContain("对话进度无法识别");
    expect(text).toContain("重新读取已保存的回复");
  });
});
