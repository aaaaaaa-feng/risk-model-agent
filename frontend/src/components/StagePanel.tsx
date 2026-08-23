import { useEffect, useState } from "react";
import { reviewLabel, statusLabel } from "../lib/labels";
import {
  BUSINESS_STAGES,
  businessStageIndex,
  nextAction,
  stageLabel,
  techStageIndex,
} from "../lib/stages";
import type { Decision, Run, RunEvent } from "../types";

/**
 * 统一的阶段详情面板，位于工作区正上方：
 * 默认紧凑展示 RUN STATUS / 当前业务阶段 / 当前技术子阶段 / NEXT ACTION；
 * 展开后包含最新事件、Audit 列表、业务阶段内子步骤进度与完整事件历史。
 * 待确认（awaiting_decision）时强制展开并高亮，引导用户到中间的 DecisionWorkbench 确认。
 */
export function StagePanel({
  run,
  decision,
  events,
}: {
  run: Run | null;
  decision: Decision | null;
  events: RunEvent[];
}) {
  const [expanded, setExpanded] = useState(false);
  const awaiting = run?.status === "awaiting_decision";

  // 进入待确认状态时自动展开，提示用户“需要你确认”
  useEffect(() => {
    if (awaiting) setExpanded(true);
  }, [awaiting]);

  if (!run)
    return (
      <section className="stage-panel idle" aria-label="当前运行阶段">
        <div className="panel-status">
          <span className="panel-label">RUN STATUS</span>
          <b className="rail-status idle">未启动</b>
        </div>
        <p className="panel-hint">
          导入本地表、完成关联并创建 Y 任务后，这里会持续显示 Agent、工具与 Reviewer 状态。
        </p>
      </section>
    );

  const groupIndex = businessStageIndex(run.stage);
  const group = groupIndex >= 0 ? BUSINESS_STAGES[groupIndex] : null;
  const currentIndex = Math.max(0, techStageIndex(run.stage));
  const latest = events.at(-1);
  const review = decision?.payload?.summary?.review || decision?.review;

  return (
    <section
      className={`stage-panel ${expanded ? "expanded" : ""} ${awaiting ? "attention" : ""}`}
      aria-label="当前运行阶段"
    >
      <div className="panel-main">
        <div className="panel-status">
          <span className="panel-label">RUN STATUS</span>
          <b className={`rail-status ${run.status}`}>{statusLabel(run.status)}</b>
        </div>
        <div className="panel-stage">
          <span className="panel-label">当前阶段</span>
          <strong>
            {group ? group.label : "—"}
            <em>
              {stageLabel(run.stage)} · {String(currentIndex + 1).padStart(2, "0")} /{" "}
              {group ? group.substages.length : "—"} 子步骤
            </em>
          </strong>
        </div>
        <div className="panel-next">
          <span className="panel-label">NEXT ACTION</span>
          <strong>
            {awaiting ? `需要你确认：${nextAction(run, decision)}` : nextAction(run, decision)}
          </strong>
        </div>
        <button
          className="panel-toggle"
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起阶段详情" : "展开阶段详情"}
        </button>
      </div>
      {awaiting && (
        <p className="panel-attention" role="status">
          当前方案等待你的确认，请在下方工作台完成选择后继续。
        </p>
      )}
      {expanded && (
        <div className="panel-detail">
          <p className="panel-latest">{latest?.summary || "等待节点事件"}</p>
          <ul className="audit-list compact">
            <li>
              <span>主 Agent</span>
              <b>{latest?.agent === "main_agent" ? "执行中" : "方案协调"}</b>
            </li>
            <li>
              <span>Reviewer</span>
              <b className={["block", "blocked"].includes(review?.status || "") ? "danger" : "ok"}>
                {review?.status
                  ? reviewLabel[review.status] || "已完成预审"
                  : run.stage === "training"
                    ? "质检中"
                    : "随节点执行"}
              </b>
            </li>
            <li>
              <span>本地工具</span>
              <b>{latest?.tool || "—"}</b>
            </li>
            <li>
              <span>Checkpoint</span>
              <b>{run.node}</b>
            </li>
            <li>
              <span>事件序号</span>
              <b>#{latest?.sequence || run.seq}</b>
            </li>
          </ul>
          {group && (
            <ol className="stage-substeps" aria-label={`${group.label}子步骤`}>
              {group.substages.map((stage) => {
                const index = techStageIndex(stage);
                const state =
                  index < currentIndex ? "done" : index === currentIndex ? "active" : "";
                return (
                  <li key={stage} className={state}>
                    <i>{index < currentIndex ? "✓" : index + 1}</i>
                    <span>{stageLabel(stage)}</span>
                  </li>
                );
              })}
            </ol>
          )}
          {events.length > 0 && (
            <div className="rail-history">
              {events
                .slice()
                .reverse()
                .map((event) => (
                  <div key={event.id}>
                    <time>{new Date(event.time).toLocaleTimeString()}</time>
                    <b>
                      {event.agent} · {event.status}
                    </b>
                    <p>{event.summary}</p>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
