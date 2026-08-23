import { formatMetric } from "../lib/format";
import { runStageLabel } from "../lib/labels";
import type { ModelResult } from "../types/model";
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
  const result = (run.state?.model_result as ModelResult) || {};
  const candidates = result.candidates || [];
  const champion = result.champion
    ? candidates.find((item) => item.candidate === result.champion)
    : undefined;
  const conditional =
    run.status === "succeeded" && champion && champion.test_monotonicity?.absolute !== true;
  if (run.status === "failed" || run.status === "blocked")
    return (
      <div className="run-workbench">
        <div className="error-panel">
          <span className="eyebrow">{run.status.toUpperCase()}</span>
          <h2>{run.status === "failed" ? "当前 Run 执行失败" : "当前 Run 已安全停止"}</h2>
          <p>其他 Y 任务不受影响；错误码和最后证据保留在事件记录中。</p>
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
      {run.status === "succeeded" && (
        <div className="run-complete">
          <strong>{conditional ? "模型已完成质检，需关注排序" : "模型已通过最终质检"}</strong>
          <p>
            {conditional
              ? "产物已生成；Test 等频分箱未达到绝对排序，报告已标记条件通过。"
              : "报告、模型包与独立评分入口已生成。"}
          </p>
        </div>
      )}
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
            <Metric label="Test AUC" value={formatMetric(champion.test_metrics?.roc_auc)} />
            <Metric label="Test KS" value={formatMetric(champion.test_metrics?.ks)} />
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
                {candidates.map((item) => (
                  <tr key={item.candidate}>
                    <td>
                      <strong>{item.candidate}</strong>
                    </td>
                    <td>
                      <span className={`status ${item.status}`}>{item.status}</span>
                    </td>
                    <td>{item.calibration || "—"}</td>
                    <td>{formatMetric(item.test_metrics?.roc_auc)}</td>
                    <td>{formatMetric(item.test_metrics?.ks)}</td>
                    <td>{formatMetric(item.train_test_score_psi)}</td>
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
            <strong>{runStageLabel[run.stage] || run.stage}</strong>
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
