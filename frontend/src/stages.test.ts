import { describe, expect, it } from "vitest";
import { businessStageIndex, businessSubstageIndex } from "@/features/runs/lib/stages";

describe("业务阶段相对进度", () => {
  it("不使用全局技术阶段下标展示子步骤", () => {
    expect(businessStageIndex("completed")).toBe(3);
    expect(businessSubstageIndex("reporting")).toBe(0);
    expect(businessSubstageIndex("completed")).toBe(1);
  });

  it("对未识别阶段返回 -1", () => {
    expect(businessSubstageIndex("unknown")).toBe(-1);
    expect(businessSubstageIndex(null)).toBe(-1);
  });
});
