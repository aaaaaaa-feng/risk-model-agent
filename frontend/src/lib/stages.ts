import { runStageLabel } from "./labels";
import type { Decision, Run } from "../types";

/**
 * 技术子阶段执行顺序，与后端 Run.stage 一一对应。
 * 顶部业务流程条只展示 4 个业务阶段；具体子阶段由 StagePanel 高亮。
 */
export const TECH_STAGES = [
  "target_confirmation",
  "data_diagnosis",
  "cleaning",
  "split",
  "screening",
  "binning",
  "model_plan",
  "code_review",
  "training",
  "reporting",
  "completed",
] as const;

export interface BusinessStage {
  id: string;
  label: string;
  substages: readonly string[];
}

/** 业务视角的 4 个阶段分组（面向风控业务人员，而非技术节点）。 */
export const BUSINESS_STAGES: readonly BusinessStage[] = [
  {
    id: "data_prep",
    label: "数据准备",
    substages: ["target_confirmation", "data_diagnosis", "cleaning"],
  },
  {
    id: "sample_features",
    label: "样本与特征",
    substages: ["split", "screening", "binning"],
  },
  {
    id: "modeling_qc",
    label: "建模与质检",
    substages: ["model_plan", "code_review", "training"],
  },
  {
    id: "reporting",
    label: "报告与交付",
    substages: ["reporting", "completed"],
  },
];

export function techStageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  return TECH_STAGES.indexOf(stage as (typeof TECH_STAGES)[number]);
}

/** 当前技术子阶段所属的业务阶段下标；未识别时返回 -1。 */
export function businessStageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  return BUSINESS_STAGES.findIndex((group) => group.substages.includes(stage));
}

/** 技术子阶段的中文标签，未知阶段回退为原始值。 */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "—";
  return runStageLabel[stage] || stage;
}

/** 从 StageRail 迁移：根据 Run 状态推导下一步动作文案。 */
export function nextAction(run: Run, decision: Decision | null): string {
  if (run.status === "awaiting_decision") return decision?.payload?.title || "确认当前方案";
  if (run.status === "succeeded") return "查看报告或批量评分";
  if (run.status === "failed") return "查看错误证据并重试";
  if (run.status === "blocked") return "Run 已停止，可基于同一 Y 重试";
  return `等待 ${stageLabel(run.stage)} 完成`;
}
