import { useState } from "react";
import { reviewLabel, statusLabel } from "../lib/labels";
import { eventSummary } from "@/shared/lib/errors";
import { Badge, statusVariant } from "@/shared/ui/badge";
import {
  BUSINESS_STAGES,
  businessStageIndex,
  businessSubstageIndex,
  nextAction,
  stageLabel,
} from "../lib/stages";
import type { Decision, Run, RunEvent } from "../types";

/**
 * 统一的阶段详情面板，位于工作区正上方：
 * 默认紧凑展示 RUN STATUS / 当前业务阶段 / 当前技术子阶段 / NEXT ACTION；
 * 展开后包含最新事件、Audit 列表、业务阶段内子步骤进度与完整事件历史。
 * 待确认时高亮；详情仅按用户操作展开，避免挤占工作区。
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
  const [eventLimit, setEventLimit] = useState(30);
  const awaiting = run?.status === "awaiting_decision";

  if (!run)
    return (
      <section className="stage-panel idle" aria-label="当前运行阶段">
        <div className="panel-status">
          <span className="panel-label">RUN STATUS</span>
          <Badge variant="muted">未启动</Badge>
        </div>
        <p className="panel-hint">导入本地数据并选择建模目标后即可开始；多表关联按需使用。</p>
      </section>
    );

  const groupIndex = businessStageIndex(run.stage);
  const group = groupIndex >= 0 ? BUSINESS_STAGES[groupIndex] : null;
  const currentIndex = Math.max(0, businessSubstageIndex(run.stage));
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
          <Badge variant={statusVariant(run.status)}>{statusLabel(run.status)}</Badge>
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
          title={
            expanded ? "收起 Agent、Reviewer 和本地工具详情" : "展开 Agent、Reviewer 和本地工具详情"
          }
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起阶段详情" : "展开阶段详情"}
        </button>
      </div>
      {awaiting && (
        <p className="panel-attention" role="status">
          当前方案等待确认。返回工作台可查看并确认，任务不会自动跳过此步骤。
        </p>
      )}
      {expanded && (
        <div className="panel-detail">
          <p className="panel-latest">
            {eventSummary(latest?.status, latest?.summary, eventErrorCode(latest))}
          </p>
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
              {group.substages.map((stage, index) => {
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
                .slice(-eventLimit)
                .reverse()
                .map((event) => (
                  <div key={event.id}>
                    <time>{new Date(event.time).toLocaleTimeString()}</time>
                    <b>
                      {event.agent} · {statusLabel(event.status)}
                    </b>
                    <p>{eventSummary(event.status, event.summary, eventErrorCode(event))}</p>
                  </div>
                ))}
              {events.length > eventLimit && (
                <button
                  className="panel-toggle"
                  onClick={() => setEventLimit((limit) => limit + 100)}
                >
                  加载更早事件（还剩 {events.length - eventLimit} 条）
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function eventErrorCode(event: RunEvent | undefined): string | undefined {
  const value = event?.evidence?.error_code;
  return typeof value === "string" ? value : undefined;
}
