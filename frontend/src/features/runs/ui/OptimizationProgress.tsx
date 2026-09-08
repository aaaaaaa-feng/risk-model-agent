import { useEffect, useState } from "react";
import { Button } from "@/shared/ui/button";
import { httpClient } from "@/shared/api/client";
import { notify } from "@/shared/lib/notify";
import { errorMessage } from "@/shared/lib/format";
import type { Run } from "../types";

interface Round {
  round_id: number;
  plan: { models: string[]; parameters: unknown; features: string[] };
  patch?: { reason: string; expected_effect: string };
  rank: [boolean, number];
  replaced_best: boolean;
  previous_delta: number | null;
  best_delta: number | null;
  candidate_fits: number;
  duration_seconds: number;
}
const stops: Record<string, string> = {
  target_met: "开发目标已达到",
  no_improvement: "连续无改善",
  repeated_plan: "方案重复",
  optimization_budget_exhausted: "优化次数用尽",
  fit_budget_exhausted: "拟合预算不足",
  time_budget_exhausted: "时间预算用尽",
  illegal_patch: "优化方案未通过边界校验",
  provider_unavailable: "模型请求不可用",
  baseline_strategy_complete: "对照流程完成",
};
export function OptimizationProgress({ run }: { run: Run }) {
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const active = ["queued", "running", "awaiting_decision"].includes(run.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const rounds = (run.state?.optimization_rounds || []) as unknown as Round[];
  const snapshot = run.state?.objective_snapshot as unknown as
    | {
        objective?: { target_value: number; max_optimizations: number; max_candidate_fits: number };
        version?: string;
      }
    | undefined;
  const goal = snapshot?.objective;
  const stop = String(run.state?.optimization_stop_reason || "");
  const cancel = async () => {
    setBusy(true);
    try {
      await httpClient.post(`/runs/${encodeURIComponent(run.id)}/cancel`, {});
      notify("已请求终止实际计算");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="progress-block" aria-label="真实优化记录">
      <h3>目标、预算与优化记录</h3>
      <p>
        {goal
          ? `开发验证 AUC 目标 ${goal.target_value}；初始一次 + 最多 ${goal.max_optimizations} 次优化；拟合预算 ${goal.max_candidate_fits}`
          : "目标与约束将在方案确认后冻结"}
      </p>
      <p>
        已完成 {rounds.length} 轮 · 实际拟合 {String(run.state?.candidate_fits_used ?? 0)} 次 ·{" "}
        {active
          ? `已等待 ${Math.max(0, Math.floor((now - Date.parse(run.created_at)) / 1000))} 秒`
          : "运行已结束"}
      </p>
      {stop && (
        <p>
          停止原因：{stops[stop] || stop}；业务目标：
          {run.state?.goal_status === "met" ? "开发指标达标，仍需查看最终 OOT" : "未达成或待验证"}
        </p>
      )}
      {active && (
        <Button variant="outline" disabled={busy} onClick={cancel}>
          取消并安全终止
        </Button>
      )}
      {rounds.map((round) => (
        <details key={round.round_id} open>
          <summary>
            第 {round.round_id + 1} 轮 · AUC {round.rank[1].toFixed(4)} ·{" "}
            {round.replaced_best ? "替换历史最佳" : "保留原最佳"}
          </summary>
          <p>
            {round.patch?.reason || "初始已审批方案"}。{round.patch?.expected_effect}
          </p>
          <p>
            执行模型：{round.plan.models.join("、")}；变量 {round.plan.features.length} 个；拟合{" "}
            {round.candidate_fits} 次；耗时 {round.duration_seconds.toFixed(1)} 秒
          </p>
          <p>
            相比上一轮：{round.previous_delta?.toFixed(4) ?? "—"}；相比此前最佳：
            {round.best_delta?.toFixed(4) ?? "—"}
          </p>
          <pre>{JSON.stringify(round.plan.parameters, null, 2)}</pre>
        </details>
      ))}
      {snapshot && (
        <details>
          <summary>查看冻结约束与版本</summary>
          <pre>{JSON.stringify(snapshot, null, 2)}</pre>
        </details>
      )}
      {run.state?.package_manifest && (
        <details>
          <summary>模型交付自检</summary>
          <pre>
            {JSON.stringify(
              (run.state.package_manifest as Record<string, unknown>).delivery_check,
              null,
              2,
            )}
          </pre>
        </details>
      )}
      <p>Test 用于开发选择；最终 OOT 在最佳模型冻结后检验。无改善也会如实保留记录。</p>
    </section>
  );
}
