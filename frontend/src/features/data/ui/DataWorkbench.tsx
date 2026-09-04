import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { dataApi } from "../api/dataApi";
import { errorMessage } from "@/shared/lib/format";
import { statusLabel } from "@/features/runs";
import { Tabs, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { Badge, statusVariant } from "@/shared/ui/badge";
import { Button, buttonVariants } from "@/shared/ui/button";
import { Checkbox } from "@/shared/ui/checkbox";
import { Input } from "@/shared/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/shared/ui/select";
import { notify } from "@/shared/lib/notify";
import { Hint } from "@/shared/ui/hint";
import { cn } from "@/shared/lib/utils";
import type { ProjectDetail } from "@/features/projects";
import type { DataAsset } from "../types";

import { AssetTable } from "./DataSections";

interface Props {
  detail: ProjectDetail;
  onRefresh: () => Promise<void>;
  onRunsStarted: (runId: string) => void;
}

interface JoinStepDraft {
  id: string;
  right_asset_id: string;
  leftKeys: string;
  rightKeys: string;
}
type DataSection = "upload" | "join" | "target";
const dataSections: ReadonlyArray<readonly [DataSection, string]> = [
  ["upload", "1 导入"],
  ["join", "2 关联"],
  ["target", "3 Y 任务"],
];

export function DataWorkbench({ detail, onRefresh, onRunsStarted }: Props) {
  const [section, setSection] = useState<DataSection>("upload");
  const [uploadKind, setUploadKind] = useState("feature");
  const [busy, setBusy] = useState("");
  const [baseId, setBaseId] = useState("");
  const [steps, setSteps] = useState<JoinStepDraft[]>([]);
  const [recommendation, setRecommendation] = useState<unknown>(null);
  const [datasetId, setDatasetId] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const assets = detail.assets.filter((item) => item.status !== "sheet_selection_required");
  const binaryCandidates = useMemo(
    () =>
      detail.dataset_versions.find((item) => item.id === datasetId)?.profile?.binary_candidates ||
      [],
    [detail.dataset_versions, datasetId],
  );
  /* eslint-disable react-hooks/exhaustive-deps */
  // Initialize defaults once when the detail shape changes; full deps would restart selection logic.
  useEffect(() => {
    if (!baseId && assets.length)
      setBaseId((assets.find((item) => item.kind === "base") || assets[0]).id);
    if (!datasetId && detail.dataset_versions.length) setDatasetId(detail.dataset_versions[0].id);
    if (!selectedTasks.length && detail.target_tasks.length)
      setSelectedTasks(
        detail.target_tasks.filter((item) => item.status === "queued").map((item) => item.id),
      );
  }, [assets.length, detail.dataset_versions.length, detail.target_tasks.length]);
  /* eslint-enable react-hooks/exhaustive-deps */

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    setBusy("upload");
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        form.append("kind", uploadKind);
        await dataApi.uploadAsset(detail.project.id, form);
      }
      await onRefresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
      event.target.value = "";
    }
  };
  const chooseSheet = async (asset: DataAsset, sheet: string) => {
    try {
      await dataApi.selectSheet(asset.id, sheet);
      await onRefresh();
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };
  const materialize = async (assetId: string) => {
    setBusy(assetId);
    try {
      await dataApi.materialize(assetId);
      await onRefresh();
      setSection("target");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const addStep = () => {
    const right = assets.find((item) => item.id !== baseId);
    setSteps((current) => [
      ...current,
      { id: crypto.randomUUID(), right_asset_id: right?.id || "", leftKeys: "", rightKeys: "" },
    ]);
    setRecommendation(null);
  };
  const recommend = async (step: JoinStepDraft) => {
    if (!baseId || !step.right_asset_id) return;
    setBusy(`recommend-${step.id}`);
    try {
      const result = await dataApi.recommendJoin(baseId, step.right_asset_id);
      const best = result.recommendations?.[0];
      setRecommendation({ stepId: step.id, ...result });
      if (best)
        setSteps((current) =>
          current.map((item) =>
            item.id === step.id
              ? {
                  ...item,
                  leftKeys: best.left_keys.join(","),
                  rightKeys: best.right_keys.join(","),
                }
              : item,
          ),
        );
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const executeJoin = async () => {
    if (!baseId || !steps.length) return;
    setBusy("join");
    try {
      const payloadSteps = steps.map((step) => ({
        right_asset_id: step.right_asset_id,
        left_keys: splitKeys(step.leftKeys),
        right_keys: splitKeys(step.rightKeys),
        how: "left",
        expected_cardinality: "many_to_one",
        suffix: "_right",
      }));
      const created = await dataApi.createJoinPlan({
        project_id: detail.project.id,
        name: `关联方案 ${new Date().toLocaleTimeString()}`,
        base_asset_id: baseId,
        steps: payloadSteps,
      });
      await dataApi.executeJoinPlan(created.join_plan.id);
      await onRefresh();
      setSection("target");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const createTargets = async () => {
    if (!datasetId || !targets.length) return;
    setBusy("targets");
    try {
      await dataApi.createTargets({
        project_id: detail.project.id,
        dataset_version_id: datasetId,
        target_columns: targets,
      });
      setTargets([]);
      await onRefresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const startRuns = async () => {
    setBusy("runs");
    let first = "";
    try {
      for (const taskId of selectedTasks) {
        const result = await dataApi.createRun({
          project_id: detail.project.id,
          target_task_id: taskId,
          mode: detail.project.mode,
        });
        first ||= result.run.id;
      }
      await onRefresh();
      if (first) onRunsStarted(first);
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="data-workbench">
      <div className="stage-line">
        <div>
          <h2>
            准备本地建模数据
            <Hint text="支持直接建模和多表关联；每个关联结果都会重新校验。" />
          </h2>
        </div>
        <div className="run-meta">
          PROJECT <b>{detail.project.id.slice(-8)}</b>
          <br />
          ASSETS <b>{detail.assets.length}</b>
        </div>
      </div>
      <Tabs
        value={section}
        onValueChange={(id) => setSection(id as DataSection)}
        className="contents"
      >
        <TabsList className="subnav" aria-label="数据准备步骤">
          {dataSections.map(([id, label]) => (
            <TabsTrigger key={id} value={id} id={`tab-${id}`}>
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {section === "upload" && (
        <section
          id="data-panel-upload"
          className="work-section"
          role="tabpanel"
          aria-labelledby="tab-upload"
        >
          <div className="section-heading">
            <div>
              <h3>
                导入 CSV / Excel
                <Hint text="可一次选择多张表；Excel 多 Sheet 会先要求选择 Sheet。" />
              </h3>
            </div>
            <div className="upload-actions">
              <Select value={uploadKind} onValueChange={setUploadKind}>
                <SelectTrigger className="w-[150px]" aria-label="文件用途">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="base">基准样本表</SelectItem>
                  <SelectItem value="feature">特征表</SelectItem>
                  <SelectItem value="dictionary">数据字典</SelectItem>
                  <SelectItem value="score_input">待评分样本</SelectItem>
                </SelectContent>
              </Select>
              <label
                className={cn(
                  buttonVariants(),
                  "file-button",
                  busy === "upload" && "cursor-not-allowed opacity-50",
                )}
                title="从本机选择一个或多个 CSV / Excel 文件导入当前项目"
              >
                {busy === "upload" ? "导入中…" : "选择本地文件"}
                <input
                  type="file"
                  multiple
                  accept=".csv,.xlsx,.xlsm,.xls"
                  onChange={upload}
                  disabled={busy === "upload"}
                />
              </label>
            </div>
          </div>
          <AssetTable
            assets={detail.assets}
            busy={busy}
            onSheet={chooseSheet}
            onMaterialize={materialize}
          />
        </section>
      )}
      {section === "join" && (
        <section
          id="data-panel-join"
          className="work-section"
          role="tabpanel"
          aria-labelledby="tab-join"
        >
          <div className="section-heading">
            <div>
              <h3>
                可视化关联工作流
                <Hint text="先由 Agent 推荐关联键，再由用户核对或修改，最后执行完整校验。" />
              </h3>
            </div>
            <Button variant="outline" onClick={addStep} disabled={assets.length < 2}>
              ＋ 添加特征表
            </Button>
          </div>
          <label className="field-inline">
            基准表
            <Select
              value={baseId}
              onValueChange={(value) => {
                setBaseId(value);
                setSteps([]);
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择基准表" />
              </SelectTrigger>
              <SelectContent>
                {assets
                  .filter((a) => a.kind !== "dictionary")
                  .map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </label>
          <div className="join-steps">
            {steps.map((step, index) => (
              <div className="join-step" key={step.id}>
                <div className="join-step-index">{String(index + 1).padStart(2, "0")}</div>
                <label>
                  右表
                  <Select
                    value={step.right_asset_id}
                    onValueChange={(value) =>
                      setSteps((current) =>
                        current.map((v) =>
                          v.id === step.id ? { ...v, right_asset_id: value } : v,
                        ),
                      )
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="选择特征表" />
                    </SelectTrigger>
                    <SelectContent>
                      {assets
                        .filter((a) => a.id !== baseId && a.kind !== "dictionary")
                        .map((a) => (
                          <SelectItem key={a.id} value={a.id}>
                            {a.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </label>
                <label>
                  左键（逗号分隔）
                  <Input
                    value={step.leftKeys}
                    onChange={(e) =>
                      setSteps((current) =>
                        current.map((v) =>
                          v.id === step.id ? { ...v, leftKeys: e.target.value } : v,
                        ),
                      )
                    }
                    placeholder="customer_id"
                  />
                </label>
                <label>
                  右键（逗号分隔）
                  <Input
                    value={step.rightKeys}
                    onChange={(e) =>
                      setSteps((current) =>
                        current.map((v) =>
                          v.id === step.id ? { ...v, rightKeys: e.target.value } : v,
                        ),
                      )
                    }
                    placeholder="customer_id"
                  />
                </label>
                <Button
                  variant="outline"
                  className="join-step-recommend"
                  onClick={() => recommend(step)}
                  disabled={busy === `recommend-${step.id}`}
                >
                  {busy === `recommend-${step.id}` ? "分析中…" : "Agent 推荐"}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="删除步骤"
                  onClick={() => setSteps((current) => current.filter((v) => v.id !== step.id))}
                >
                  ×
                </Button>
              </div>
            ))}
          </div>
          {recommendation !== null && (
            <div className="review-strip">
              <strong>Agent 推荐</strong>
              <span>
                {(recommendation as { recommendations?: unknown[] }).recommendations?.length
                  ? `已按重合率和唯一性填入推荐键；仍需执行完整校验。`
                  : "没有可靠推荐，请手动填写并核对关联键。"}
              </span>
            </div>
          )}
          <div className="inline-actions">
            <Button
              onClick={executeJoin}
              disabled={!steps.length || busy === "join"}
              title="按当前关联键执行多表关联并运行粒度与样本膨胀校验"
            >
              {busy === "join" ? "关联校验中…" : "执行关联并校验"}
            </Button>
          </div>
        </section>
      )}
      {section === "target" && (
        <section
          id="data-panel-target"
          className="work-section"
          role="tabpanel"
          aria-labelledby="tab-target"
        >
          <div className="section-heading">
            <div>
              <h3>
                创建多个 Y 任务
                <Hint text="-1 和空值会按每个 Y 独立排除；一个 Y 阻断不影响其他任务。" />
              </h3>
            </div>
          </div>
          {detail.dataset_versions.length === 0 ? (
            <Empty text="请先把原始表物化，或完成多表关联。" />
          ) : (
            <>
              <label>
                建模数据版本
                <Select
                  value={datasetId}
                  onValueChange={(value) => {
                    setDatasetId(value);
                    setTargets([]);
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="选择版本" />
                  </SelectTrigger>
                  <SelectContent>
                    {detail.dataset_versions.map((item) => (
                      <SelectItem value={item.id} key={item.id}>
                        {item.label} · {item.rows.toLocaleString()}×{item.columns}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <div className="target-candidates">
                {binaryCandidates.length ? (
                  binaryCandidates.map((column) => (
                    <label key={column}>
                      <Checkbox
                        checked={targets.includes(column)}
                        onCheckedChange={(checked) =>
                          setTargets((current) =>
                            checked === true
                              ? [...current, column]
                              : current.filter((v) => v !== column),
                          )
                        }
                      />
                      {column}
                    </label>
                  ))
                ) : (
                  <p>该版本没有识别到同时包含 0/1 的候选 Y。</p>
                )}
              </div>
              <Button disabled={!targets.length || busy === "targets"} onClick={createTargets}>
                {busy === "targets" ? "创建中…" : `创建 ${targets.length || ""} 个 Y 任务`}
              </Button>
            </>
          )}
          {detail.target_tasks.length > 0 && (
            <div className="task-queue">
              <div className="section-heading">
                <div>
                  <h3>
                    Y 任务队列
                    <Hint text="可一次启动多个任务，本地 Worker 按顺序执行。" />
                  </h3>
                </div>
              </div>
              {detail.target_tasks.map((task) => (
                <label className="task-row" key={task.id}>
                  <Checkbox
                    disabled={!["queued", "failed", "blocked"].includes(task.status)}
                    checked={selectedTasks.includes(task.id)}
                    onCheckedChange={(checked) =>
                      setSelectedTasks((current) =>
                        checked === true
                          ? [...current, task.id]
                          : current.filter((v) => v !== task.id),
                      )
                    }
                  />
                  <strong>{task.target_column}</strong>
                  <span>{task.valid_sample_count.toLocaleString()} 有效样本</span>
                  <Badge variant={statusVariant(task.status)}>{statusLabel(task.status)}</Badge>
                </label>
              ))}
              <Button disabled={!selectedTasks.length || busy === "runs"} onClick={startRuns}>
                {busy === "runs" ? "入队中…" : `启动 ${selectedTasks.length} 个 Run`}
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <span>EMPTY</span>
      <p>{text}</p>
    </div>
  );
}
function splitKeys(value: string) {
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}
