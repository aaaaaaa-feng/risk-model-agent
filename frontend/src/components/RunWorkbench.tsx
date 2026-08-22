import type { Run, RunEvent } from "../types";

export function RunWorkbench({
  run,
  events,
  onRetry,
}: {
  run: Run;
  events: RunEvent[];
  onRetry?: () => void;
}) {
  const state = run.state || {};
  const result = state.model_result || {};
  const champion = result.candidates?.find((item: any) => item.candidate === result.champion);
  const conditional =
    run.status === "succeeded" && champion && champion.test_monotonicity?.absolute !== true;
  if (run.status === "failed" || run.status === "blocked")
    return (
      <div className="run-workbench">
        <div className="stage-line">
          <div>
            <span className="eyebrow">{run.status.toUpperCase()}</span>
            <h2>{run.status === "failed" ? "当前 Run 执行失败" : "当前 Run 已安全停止"}</h2>
            <p>其他 Y 任务不受影响；错误码和最后证据保留在事件记录中。</p>
          </div>
          <div className="run-meta">
            RUN <b>{run.id.slice(-8)}</b>
            <br />
            NODE <b>{run.node}</b>
          </div>
        </div>
        <div className="error-panel">
          <strong>{run.error || "USER_REJECTED"}</strong>
          <p>{events.at(-1)?.summary}</p>
          {onRetry && (
            <button className="button primary" onClick={onRetry}>
              基于同一 Y 新建 Run
            </button>
          )}
        </div>
      </div>
    );
  return (
    <div className="run-workbench">
      <div className="stage-line">
        <div>
          <span className="eyebrow">
            {run.status === "succeeded" ? "RUN COMPLETED" : "LOCAL WORKER RUNNING"}
          </span>
          <h2>
            {run.status === "succeeded"
              ? conditional
                ? "模型已完成质检，需关注排序"
                : "模型已通过最终质检"
              : stageLabel(run.stage)}
          </h2>
          <p>
            {run.status === "succeeded"
              ? conditional
                ? "产物已生成；Test 等频分箱未达到绝对排序，报告已标记条件通过。"
                : "报告、模型包与独立评分入口已生成。"
              : events.at(-1)?.summary || "等待本地节点反馈…"}
          </p>
        </div>
        <div className="run-meta">
          RUN <b>{run.id.slice(-8)}</b>
          <br />
          CHECKPOINT <b>{run.node}</b>
        </div>
      </div>
      <div className="progress-block">
        <div>
          <span>整体进度</span>
          <b>{Math.round((run.progress || 0) * 100)}%</b>
        </div>
        <progress max={1} value={run.progress || 0} />
      </div>
      {champion ? (
        <>
          <div className="summary-grid four">
            <Metric label="Champion" value={champion.candidate} />
            <Metric label="Test AUC" value={metric(champion.test_metrics?.roc_auc)} />
            <Metric label="Test KS" value={metric(champion.test_metrics?.ks)} />
            <Metric label="绝对排序" value={champion.test_monotonicity?.absolute ? "是" : "否"} />
          </div>
          <div className="section-heading">
            <div>
              <h3>候选模型执行矩阵</h3>
              <p>OOT 只在 Champion 冻结后进入最终报告。</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>模型</th>
                  <th>状态</th>
                  <th>校准</th>
                  <th>Test AUC</th>
                  <th>Test KS</th>
                  <th>PSI</th>
                </tr>
              </thead>
              <tbody>
                {result.candidates.map((item: any) => (
                  <tr key={item.candidate}>
                    <td>
                      <strong>{item.candidate}</strong>
                    </td>
                    <td>
                      <span className={`status ${item.status}`}>{item.status}</span>
                    </td>
                    <td>{item.calibration || "—"}</td>
                    <td>{metric(item.test_metrics?.roc_auc)}</td>
                    <td>{metric(item.test_metrics?.ks)}</td>
                    <td>{metric(item.train_test_score_psi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="live-node">
          <div className="pulse" />
          <div>
            <strong>{stageLabel(run.stage)}</strong>
            <p>页面关闭不会停止任务。所有拟合、统计与报告均在本机执行。</p>
          </div>
        </div>
      )}
      <div className="event-preview">
        <h3>最近事件</h3>
        {events
          .slice(-6)
          .reverse()
          .map((event) => (
            <div key={event.id}>
              <time>{new Date(event.time).toLocaleTimeString()}</time>
              <span>{event.agent}</span>
              <p>{event.summary}</p>
            </div>
          ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-cell">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function metric(value: any) {
  return value == null ? "—" : Number(value).toFixed(4);
}
export function stageLabel(stage: string) {
  return (
    (
      {
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
      } as Record<string, string>
    )[stage] || stage
  );
}
