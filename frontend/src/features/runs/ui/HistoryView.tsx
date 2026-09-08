import { useState } from "react";
import { httpClient } from "@/shared/api/client";
import { errorMessage } from "@/shared/lib/format";
import { notify } from "@/shared/lib/notify";
import { runStageLabel, statusLabel } from "../lib/labels";
import { Badge, statusVariant } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Hint } from "@/shared/ui/hint";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import type { TargetTask } from "@/features/data";
import type { Run } from "../types";

export function HistoryView({
  runs,
  tasks,
  selectedId,
  onSelect,
}: {
  runs: Run[];
  tasks: TargetTask[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<{
    directly_comparable: boolean;
    reasons: string[];
    runs: {
      run_id: string;
      plan: unknown;
      metrics: unknown;
      duration_seconds: number;
      model_calls: number;
      candidate_fits: number;
      cost_usd: number | null;
      stop_reason: string;
    }[];
  } | null>(null);
  const compare = async () => {
    try {
      setComparison(
        await httpClient.get(
          `/runs/compare?left=${encodeURIComponent(comparisonIds[0])}&right=${encodeURIComponent(comparisonIds[1])}`,
        ),
      );
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };
  const target = new Map(tasks.map((item) => [item.id, item.target_column]));
  return (
    <div className="history-view">
      <div className="stage-line">
        <div>
          <h2>
            历史 Run 与只读证据
            <Hint text="新 Run 不覆盖旧记录；失败和阻断同样保留。" />
          </h2>
        </div>
        <div className="run-meta">
          TOTAL <b>{runs.length}</b>
        </div>
      </div>
      <div className="inline-actions">
        <Button disabled={comparisonIds.length !== 2} onClick={compare}>
          比较所选两次运行
        </Button>
        <span>数据、划分、目标或预算不同会标记不可直接比较</span>
      </div>
      {comparison && (
        <section>
          <h3>{comparison.directly_comparable ? "符合直接比较条件" : "不可直接比较"}</h3>
          <p>{comparison.reasons.join("、")}</p>
          <div className="table-wrap">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>运行</TableHead>
                  <TableHead>实际拟合 / 模型调用</TableHead>
                  <TableHead>训练秒数 / 费用</TableHead>
                  <TableHead>停止原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {comparison.runs.map((item) => (
                  <TableRow key={item.run_id}>
                    <TableCell>{item.run_id.slice(-10)}</TableCell>
                    <TableCell>
                      {item.candidate_fits ?? "未知"} / {item.model_calls}
                    </TableCell>
                    <TableCell>
                      {item.duration_seconds.toFixed(1)} / {item.cost_usd ?? "未知"}
                    </TableCell>
                    <TableCell>{item.stop_reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {comparison.runs.map((item) => (
            <details key={item.run_id}>
              <summary>{item.run_id.slice(-10)} 方案、变量与指标</summary>
              <pre>{JSON.stringify({ plan: item.plan, metrics: item.metrics }, null, 2)}</pre>
            </details>
          ))}
        </section>
      )}
      {runs.length === 0 ? (
        <div className="empty-state">
          <span>EMPTY</span>
          <p>还没有 Run。</p>
        </div>
      ) : (
        <div className="table-wrap history-table">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Y</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>最后阶段</TableHead>
                <TableHead>进度</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow className={run.id === selectedId ? "selected" : ""} key={run.id}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={`选择比较 ${run.id}`}
                      checked={comparisonIds.includes(run.id)}
                      disabled={comparisonIds.length >= 2 && !comparisonIds.includes(run.id)}
                      onChange={(event) => {
                        setComparison(null);
                        setComparisonIds((ids) =>
                          event.target.checked
                            ? [...ids, run.id]
                            : ids.filter((id) => id !== run.id),
                        );
                      }}
                    />
                    <code>{run.id.slice(-10)}</code>
                  </TableCell>
                  <TableCell>{target.get(run.target_task_id) || "—"}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(run.status)}>{statusLabel(run.status)}</Badge>
                  </TableCell>
                  <TableCell>{runStageLabel[run.stage]}</TableCell>
                  <TableCell>{Math.round((run.progress || 0) * 100)}%</TableCell>
                  <TableCell>{new Date(run.updated_at).toLocaleString()}</TableCell>
                  <TableCell>
                    <Button variant="link" size="sm" onClick={() => onSelect(run.id)}>
                      查看
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
