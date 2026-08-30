import { formatMetric } from "@/shared/lib/format";
import { runStageLabel, statusLabel } from "../lib/labels";
import { eventSummary, translateError } from "@/shared/lib/errors";
import { Badge, statusVariant } from "@/shared/ui/badge";
import { Hint } from "@/shared/ui/hint";
import { Button } from "@/shared/ui/button";
import { Progress } from "@/shared/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import type { ModelResult } from "../modelResult";
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
  if (run.status === "failed" || run.status === "blocked") {
    const failure = translateError(
      { code: run.error || "USER_REJECTED", message: events.at(-1)?.summary },
      { context: "model" },
    );
    return (
      <div className="run-workbench">
        <div className="error-panel">
          <span className="eyebrow">{run.status === "failed" ? "执行异常" : "安全停止"}</span>
          <h2>{run.status === "failed" ? "当前 Run 执行失败" : "当前 Run 已安全停止"}</h2>
          <p>其他 Y 任务不受影响；错误码和最后证据保留在事件记录中。</p>
          <strong title={failure.code ? `诊断码：${failure.code}` : undefined}>
            {failure.summary}
          </strong>
          {failure.action && <p>{failure.action}</p>}
          {onRetry && <Button onClick={onRetry}>基于同一 Y 新建 Run</Button>}
        </div>
      </div>
    );
  }
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
        <Progress
          className="mt-[5px] h-[9px] rounded-full bg-[var(--blue-subtle)]"
          value={Math.round((run.progress || 0) * 100)}
        />
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
              <h3>
                候选模型执行矩阵
                <Hint text="OOT 只在 Champion 冻结后进入最终报告。" />
              </h3>
            </div>
          </div>
          <div className="table-wrap">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>模型</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>校准</TableHead>
                  <TableHead>Test AUC</TableHead>
                  <TableHead>Test KS</TableHead>
                  <TableHead>PSI</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((item) => (
                  <TableRow key={item.candidate}>
                    <TableCell>
                      <strong>{item.candidate}</strong>
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(item.status)}>{statusLabel(item.status)}</Badge>
                    </TableCell>
                    <TableCell>{item.calibration || "—"}</TableCell>
                    <TableCell>{formatMetric(item.test_metrics?.roc_auc)}</TableCell>
                    <TableCell>{formatMetric(item.test_metrics?.ks)}</TableCell>
                    <TableCell>{formatMetric(item.train_test_score_psi)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      ) : (
        <div className="live-node">
          <div className="pulse" />
          <div>
            <strong>
              {runStageLabel[run.stage] || run.stage}
              <Hint text="页面关闭不会停止任务。所有拟合、统计与报告均在本机执行。" />
            </strong>
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
              <p>{eventSummary(event.status, event.summary, eventErrorCode(event))}</p>
            </div>
          ))}
      </div>
    </div>
  );
}

function eventErrorCode(event: RunEvent): string | undefined {
  const value = event.evidence?.error_code;
  return typeof value === "string" ? value : undefined;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="summary-cell">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
