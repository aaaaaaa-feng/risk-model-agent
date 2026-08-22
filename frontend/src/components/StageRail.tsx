import { useState } from "react";
import { reviewLabel, runStageLabel, statusLabel } from "../lib/labels";
import type { Decision, Run, RunEvent } from "../types";

const stages = [
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
];

export function StageRail({
  run,
  decision,
  events,
}: {
  run: Run | null;
  decision: Decision | null;
  events: RunEvent[];
}) {
  const [history, setHistory] = useState(false);
  if (!run)
    return (
      <aside className="stage-rail" aria-label="当前运行阶段">
        <div className="rail-head">
          <span>RUN STATUS</span>
          <b className="rail-status idle">未启动</b>
        </div>
        <div className="stage-box">
          <span className="eyebrow">NEXT</span>
          <h2>准备项目数据</h2>
          <p>导入本地表、完成关联并创建 Y 任务后，这里会持续显示 Agent、工具与 Reviewer 状态。</p>
        </div>
      </aside>
    );
  const currentIndex = Math.max(0, stages.indexOf(run.stage));
  const latest = events.at(-1);
  const review = decision?.payload?.summary?.review || decision?.review;
  return (
    <aside className="stage-rail" aria-label="当前运行阶段">
      <div className="rail-head">
        <span>RUN STATUS</span>
        <b className={`rail-status ${run.status}`}>{statusLabel(run.status)}</b>
      </div>
      <div className="stage-box">
        <span className="eyebrow">
          当前阶段 · {String(currentIndex + 1).padStart(2, "0")} / {stages.length}
        </span>
        <h2>{runStageLabel[run.stage]}</h2>
        <p>{latest?.summary || "等待节点事件"}</p>
      </div>
      <ul className="audit-list">
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
      <div className="next-action">
        <span>NEXT ACTION</span>
        <strong>{nextAction(run, decision)}</strong>
      </div>
      <ol className="stage-mini-map">
        {stages.map((stage, index) => (
          <li
            key={stage}
            className={index < currentIndex ? "done" : index === currentIndex ? "active" : ""}
          >
            <i>{index < currentIndex ? "✓" : index + 1}</i>
            <span>{runStageLabel[stage]}</span>
          </li>
        ))}
      </ol>
      <button
        className="history-toggle"
        type="button"
        aria-expanded={history}
        onClick={() => setHistory((v) => !v)}
      >
        {history ? "收起完整事件记录" : "查看完整事件记录"}
      </button>
      {history && (
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
    </aside>
  );
}

function nextAction(run: Run, decision: Decision | null) {
  if (run.status === "awaiting_decision") return decision?.payload?.title || "确认当前方案";
  if (run.status === "succeeded") return "查看报告或批量评分";
  if (run.status === "failed") return "查看错误证据并重试";
  return `等待 ${runStageLabel[run.stage]} 完成`;
}
