import { runStageLabel } from "../lib/labels";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Run, TargetTask } from "../types";

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
  const target = new Map(tasks.map((item) => [item.id, item.target_column]));
  return (
    <div className="history-view">
      <div className="stage-line">
        <div>
          <h2>历史 Run 与只读证据</h2>
          <p>新 Run 不覆盖旧记录；失败和阻断同样保留。</p>
        </div>
        <div className="run-meta">
          TOTAL <b>{runs.length}</b>
        </div>
      </div>
      {runs.length === 0 ? (
        <div className="empty-state">
          <span>EMPTY</span>
          <p>还没有 Run。</p>
        </div>
      ) : (
        <div className="table-wrap history-table">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Y</th>
                <th>状态</th>
                <th>最后阶段</th>
                <th>进度</th>
                <th>更新时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr className={run.id === selectedId ? "selected" : ""} key={run.id}>
                  <td>
                    <code>{run.id.slice(-10)}</code>
                  </td>
                  <td>{target.get(run.target_task_id) || "—"}</td>
                  <td>
                    <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
                  </td>
                  <td>{runStageLabel[run.stage]}</td>
                  <td>{Math.round((run.progress || 0) * 100)}%</td>
                  <td>{new Date(run.updated_at).toLocaleString()}</td>
                  <td>
                    <Button variant="link" size="sm" onClick={() => onSelect(run.id)}>
                      查看
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
