import { useState } from "react";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { httpClient } from "@/shared/api/client";
import { notify } from "@/shared/lib/notify";
import { errorMessage } from "@/shared/lib/format";

export interface RunObjective {
  target_value: number;
  max_optimizations: number;
  max_candidate_fits: number;
  max_seconds: number;
  strategy: "agent" | "fixed" | "parameter_search";
}
interface Readiness {
  executable: boolean;
  blockers: { code: string; action: string }[];
  warnings: string[];
  mode: string;
  provider: { configured: boolean; connectivity: string };
  sample: unknown;
}
export function RunPreflight({
  projectId,
  taskIds,
  objective,
  onChange,
  onReady,
}: {
  projectId: string;
  taskIds: string[];
  objective: RunObjective;
  onChange: (value: RunObjective) => void;
  onReady: (ready: boolean) => void;
}) {
  const [results, setResults] = useState<Readiness[]>([]);
  const [busy, setBusy] = useState(false);
  const change = (key: keyof RunObjective, value: number | string) => {
    onChange({ ...objective, [key]: value });
    onReady(false);
    setResults([]);
  };
  const diagnose = async (testProvider: boolean) => {
    setBusy(true);
    onReady(false);
    try {
      const values: Readiness[] = [];
      for (const task of taskIds)
        values.push(
          await httpClient.post<Readiness>("/runs/preflight", {
            project_id: projectId,
            target_task_id: task,
            objective,
            test_provider: testProvider,
          }),
        );
      setResults(values);
      onReady(values.length > 0 && values.every((value) => value.executable));
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="运行前诊断">
      <h3>运行目标与约束</h3>
      <p>示例目标不代表行业标准。优化仅使用开发验证证据，最终 OOT 不参与调优。</p>
      <label>
        开发验证 AUC 目标
        <Input
          type="number"
          min={0.5}
          max={1}
          step={0.01}
          value={objective.target_value}
          onChange={(event) => change("target_value", Number(event.target.value))}
        />
      </label>
      <label>
        最多优化次数（初始建模另计）
        <Input
          type="number"
          min={0}
          max={3}
          value={objective.max_optimizations}
          onChange={(event) => change("max_optimizations", Number(event.target.value))}
        />
      </label>
      <label>
        候选拟合上限
        <Input
          type="number"
          min={1}
          max={10000}
          value={objective.max_candidate_fits}
          onChange={(event) => change("max_candidate_fits", Number(event.target.value))}
        />
      </label>
      <label>
        建模优化时间上限（秒）
        <Input
          type="number"
          min={1}
          max={3600}
          value={objective.max_seconds}
          onChange={(event) => change("max_seconds", Number(event.target.value))}
        />
      </label>
      <label>
        实验方法
        <select
          value={objective.strategy}
          onChange={(event) => change("strategy", event.target.value)}
        >
          <option value="agent">诊断后自主优化</option>
          <option value="fixed">固定流程对照</option>
          <option value="parameter_search">参数搜索对照</option>
        </select>
      </label>
      <div className="inline-actions">
        <Button
          variant="outline"
          disabled={busy || !taskIds.length}
          onClick={() => diagnose(false)}
        >
          检查本地条件
        </Button>
        <Button variant="outline" disabled={busy || !taskIds.length} onClick={() => diagnose(true)}>
          检查并测试模型连接
        </Button>
      </div>
      {results.map((value, index) => (
        <div key={index} role="status">
          <p>
            目标 {index + 1}：{value.executable ? "本地执行条件满足" : "存在阻断"} · {value.mode} ·
            模型连接{" "}
            {value.provider.connectivity === "passed"
              ? "测试通过"
              : value.provider.connectivity === "failed"
                ? "测试失败"
                : "未测试"}
          </p>
          {value.blockers.map((item) => (
            <p key={item.code}>
              {item.code}：{item.action}
            </p>
          ))}
          {value.warnings.map((item) => (
            <p key={item}>{item}</p>
          ))}
          <details>
            <summary>查看样本诊断</summary>
            <pre>{JSON.stringify(value.sample, null, 2)}</pre>
          </details>
        </div>
      ))}
    </section>
  );
}
