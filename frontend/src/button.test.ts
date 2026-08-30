import { describe, expect, it } from "vitest";
import { buttonVariants } from "./components/ui/button";

describe("共享按钮禁用态", () => {
  it("保留鼠标悬停以显示 title，但明确表达不可点击", () => {
    const classes = buttonVariants();
    expect(classes).toContain("disabled:cursor-not-allowed");
    expect(classes).not.toContain("disabled:pointer-events-none");
  });
});
