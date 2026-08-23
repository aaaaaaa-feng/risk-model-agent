import { runStageLabel } from "../lib/labels";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
                    <code>{run.id.slice(-10)}</code>
                  </TableCell>
                  <TableCell>{target.get(run.target_task_id) || "—"}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
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
