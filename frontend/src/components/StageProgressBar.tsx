import { BUSINESS_STAGES, businessStageIndex, stageLabel } from "../lib/stages";
import type { Run } from "../types";

/**
 * 顶部业务流程条：只展示 4 个业务阶段，当前阶段高亮并带传导动画；
 * 具体技术子阶段由 StagePanel 展示，这里通过 tooltip 提示。
 */
export function StageProgressBar({ run }: { run: Run | null }) {
  const current = businessStageIndex(run?.stage);
  const awaiting = run?.status === "awaiting_decision";
  return (
    <nav className="stage-progress" aria-label="业务流程阶段">
      <ol>
        {BUSINESS_STAGES.map((group, index) => {
          const state = current < 0 ? "todo" : index < current ? "done" : index === current ? "current" : "todo";
          const subTip = group.substages.map(stageLabel).join(" / ");
          return (
            <li
              key={group.id}
              className={`stage-step ${state}`}
              aria-current={state === "current" ? "step" : undefined}
              title={`${group.label}：${subTip}`}
            >
              <i className="step-index">{state === "done" ? "✓" : index + 1}</i>
              <span className="step-label">{group.label}</span>
              {state === "current" && awaiting && <b className="step-badge">待确认</b>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
