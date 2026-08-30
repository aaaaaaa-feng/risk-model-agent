export const runStageLabel: Record<string, string> = {
  project_setup: "项目初始化",
  target_confirmation: "Y 确认",
  data_diagnosis: "建模前诊断",
  cleaning: "数据清洗",
  split: "样本切分",
  screening: "变量筛选",
  binning: "变量分箱",
  model_plan: "建模方案",
  code_review: "代码生成与质检",
  training: "训练、调参与校准",
  reporting: "报告与模型包",
  completed: "已完成",
};

export const decisionStageName: Record<string, string> = {
  target_confirmation: "Y 确认",
  data_diagnosis: "数据诊断与清洗",
  split: "样本切分",
  screening: "变量筛选",
  binning: "变量分箱",
  model_plan: "建模方案",
};

export const reviewLabel: Record<string, string> = {
  pass: "预审通过",
  deterministic_pass: "确定性规则通过",
  llm_reviewer_pass: "LLM Reviewer 通过",
  fallback_pass: "本地降级质检通过",
  conditional_pass: "有条件通过",
  revise: "建议调整",
  block: "发现阻断",
  blocked: "发现阻断",
};

export const confirmLabel: Record<string, string> = {
  confirm_target: "确认 Y 并继续诊断",
  confirm_data: "确认清洗并继续",
  confirm_split: "确认切分并执行",
  confirm_screening: "冻结变量并继续",
  confirm_binning: "冻结分箱并继续",
  confirm_models: "确认方案并开始训练",
};

export function statusLabel(status: string | undefined, fallback?: string): string {
  const map: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    awaiting_decision: "等待确认",
    approved: "已批准",
    succeeded: "已完成",
    failed: "失败",
    blocked: "已停止",
    ready: "已就绪",
    trained: "训练完成",
    skipped: "已跳过",
    started: "已开始",
    completed: "已完成",
    delta: "生成中",
    waiting: "等待中",
    pending: "待处理",
    active: "使用中",
    archived: "已归档",
    available: "可用",
    unavailable: "不可用",
    pass: "已通过",
    conditional_pass: "有条件通过",
  };
  return map[(status || "").toLowerCase()] || fallback || "状态未知";
}

export function monotonicLabel(rates: number[], monotonic: boolean | undefined): string {
  if (!monotonic) return "非单调（需要调整或业务例外）";
  if (rates.length < 3) return "单调（箱数较少）";
  const differences = rates.slice(1).map((value, index) => value - rates[index]);
  if (differences.every((value) => value >= -1e-12)) return "单调递增（坏率）";
  if (differences.every((value) => value <= 1e-12)) return "单调递减（坏率）";
  return "单调性标记与趋势不一致，请复核";
}
