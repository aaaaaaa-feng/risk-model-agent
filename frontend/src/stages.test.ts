import { describe, expect, it } from "vitest";
import {
  BUSINESS_STAGES,
  TECH_STAGES,
  businessStageIndex,
  businessSubstageIndex,
  stageLabel,
} from "@/features/runs/lib/stages";

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

  it("新流程不再展示代码生成阶段，但历史 Run 仍有停用标签", () => {
    expect(TECH_STAGES).not.toContain("code_review");
    expect(BUSINESS_STAGES.flatMap((group) => group.substages)).not.toContain("code_review");
    expect(stageLabel("code_review")).toBe("旧版代码质检（已停用）");
  });
});
