import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { controlHint } from "@/shared/lib/controlHint";

describe("按钮悬停说明", () => {
  it("优先使用 aria-label 作为图标按钮说明", () => {
    expect(controlHint(createElement("svg"), "关闭设置")).toBe("关闭设置");
  });

  it("可从嵌套子节点提取可见文案", () => {
    const content = createElement("span", null, "创建", createElement("b", null, "项目"));
    expect(controlHint(content, undefined)).toBe("创建 项目");
  });
});
