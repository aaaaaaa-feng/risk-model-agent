import { RunPreflight, type RunObjective } from "./RunPreflight";
import { ChangeEvent, useMemo, useRef, useState } from "react";
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
import { initialDatasetId, runnableSelection } from "../lib/workflow";

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
  ["join", "2 关联（可选）"],
  ["target", "3 目标与启动"],
];

export function DataWorkbench({ detail, onRefresh, onRunsStarted }: Props) {
  const [section, setSection] = useState<DataSection>(
    detail.dataset_versions.length ? "target" : "upload",
  );
  const [uploadKind, setUploadKind] = useState("base");
  const [busy, setBusy] = useState("");
  const [baseId, setBaseId] = useState(
    detail.assets.find((asset) => asset.kind === "base" && asset.status === "ready")?.id || "",
  );
  const [steps, setSteps] = useState<JoinStepDraft[]>([]);
  const [recommendation, setRecommendation] = useState<unknown>(null);
  const [datasetId, setDatasetId] = useState(() =>
    initialDatasetId(detail.dataset_versions, detail.target_tasks),
  );
  const [targets, setTargets] = useState<string[]>([]);
  const [selectedTasks, setSelectedTasks] = useState<string[]>(() =>
    detail.target_tasks.filter((task) => task.status === "queued").map((task) => task.id),
  );
  const queueRef = useRef<HTMLDivElement>(null);
  const assets = detail.assets.filter(
    (item) => item.status === "ready" && ["base", "feature"].includes(item.kind),
  );
  const activeDatasetId =
    datasetId || initialDatasetId(detail.dataset_versions, detail.target_tasks);
  const activeBaseId =
    baseId || assets.find((asset) => asset.kind === "base")?.id || assets[0]?.id || "";
  const currentTasks = detail.target_tasks.filter(
    (task) => task.dataset_version_id === activeDatasetId,
  );
  const selectedTaskIds = runnableSelection(selectedTasks, detail.target_tasks, activeDatasetId);
  const existingTargets = new Set(currentTasks.map((task) => task.target_column));
  const binaryCandidates = useMemo(
    () =>
      detail.dataset_versions.find((item) => item.id === activeDatasetId)?.profile
        ?.binary_candidates || [],
    [detail.dataset_versions, activeDatasetId],
  );

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
      notify("文件已导入。可直接使用单表建模，或先关联特征表。");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
      event.target.value = "";
      await onRefresh();
    }
  };
  const chooseSheet = async (asset: DataAsset, sheet: string) => {
    setBusy(asset.id);
    try {
      await dataApi.selectSheet(asset.id, sheet);
      await onRefresh();
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const materialize = async (assetId: string) => {
    setBusy(assetId);
    try {
      const result = await dataApi.materialize(assetId);
      setDatasetId(result.dataset_version.id);
      setTargets([]);
      setSelectedTasks([]);
      await onRefresh();
      setSection("target");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const addStep = () => {
    const right = assets.find((item) => item.id !== activeBaseId);
    setSteps((current) => [
      ...current,
      { id: crypto.randomUUID(), right_asset_id: right?.id || "", leftKeys: "", rightKeys: "" },
    ]);
    setRecommendation(null);
  };
  const recommend = async (step: JoinStepDraft) => {
    if (!activeBaseId || !step.right_asset_id) return;
    setBusy(`recommend-${step.id}`);
    try {
      const result = await dataApi.recommendJoin(activeBaseId, step.right_asset_id);
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
    if (!activeBaseId || !steps.length) return;
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
        base_asset_id: activeBaseId,
        steps: payloadSteps,
      });
      const result = await dataApi.executeJoinPlan(created.join_plan.id);
      setDatasetId(result.dataset_version.id);
      setTargets([]);
      setSelectedTasks([]);
      await onRefresh();
      setSection("target");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const createTargets = async () => {
    if (!activeDatasetId || !targets.length) return;
    setBusy("targets");
    try {
      const result = await dataApi.createTargets({
        project_id: detail.project.id,
        dataset_version_id: activeDatasetId,
        target_columns: targets,
      });
      setTargets([]);
      setSelectedTasks(result.target_tasks.map((task) => task.id));
      await onRefresh();
      window.requestAnimationFrame(() => queueRef.current?.scrollIntoView({ block: "nearest" }));
      notify("目标任务已创建并选中，可以开始建模。");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const [objective, setObjective] = useState<RunObjective>({
    target_value: 0.75,
    max_optimizations: 3,
    max_candidate_fits: 300,
    max_seconds: 600,
    strategy: "agent",
  });
  const [readyKey, setReadyKey] = useState("");
  const currentReadinessKey = JSON.stringify({ tasks: selectedTaskIds, objective });
  const startRuns = async () => {
    setBusy("runs");
    let first = "";
    try {
      for (const taskId of selectedTaskIds) {
        const result = await dataApi.createRun({
          project_id: detail.project.id,
          target_task_id: taskId,
          mode: detail.project.mode,
          objective,
          request_key: crypto.randomUUID(),
        });
        first ||= result.run.id;
        setSelectedTasks((current) => current.filter((id) => id !== taskId));
      }
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      await onRefresh();
      if (first) onRunsStarted(first);
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
          <p className="stage-description">导入数据 → 选择建模目标 → 确认样本与方案 → 建模和导出</p>
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
          {assets.length > 0 && (
            <div className="workflow-next">
              <p>
                单表已包含目标和特征时，点击对应文件的“用此表建模”。只有多张表需要合并时才进入关联。
              </p>
              <div className="inline-actions">
                {assets.length > 1 && (
                  <Button
                    variant="outline"
                    onClick={() => setSection("join")}
                    disabled={Boolean(busy)}
                  >
                    关联多张表
                  </Button>
                )}
                {detail.dataset_versions.length > 0 && (
                  <Button onClick={() => setSection("target")} disabled={Boolean(busy)}>
                    继续选择建模目标
                  </Button>
                )}
              </div>
            </div>
          )}
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
            <Button
              variant="outline"
              onClick={addStep}
              disabled={assets.length < 2 || Boolean(busy)}
            >
              ＋ 添加特征表
            </Button>
          </div>
          <label className="field-inline">
            基准表
            <Select
              value={activeBaseId}
              disabled={Boolean(busy)}
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
                    disabled={Boolean(busy)}
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
                        .filter((a) => a.id !== activeBaseId)
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
                    disabled={Boolean(busy)}
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
                    disabled={Boolean(busy)}
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
                  disabled={Boolean(busy)}
                >
                  {busy === `recommend-${step.id}` ? "分析中…" : "Agent 推荐"}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="删除步骤"
                  disabled={Boolean(busy)}
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
              disabled={
                !steps.length ||
                Boolean(busy) ||
                steps.some(
                  (step) =>
                    !step.right_asset_id ||
                    !splitKeys(step.leftKeys).length ||
                    splitKeys(step.leftKeys).length !== splitKeys(step.rightKeys).length,
                )
              }
              title="按当前关联键执行多表关联并运行粒度与样本膨胀校验"
            >
              {busy === "join" ? "关联校验中…" : "执行关联并校验"}
            </Button>
            <Button variant="outline" onClick={() => setSection("upload")} disabled={Boolean(busy)}>
              返回导入
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
                选择建模目标（Y）
                <Hint text="-1 和空值会按每个 Y 独立排除；一个 Y 阻断不影响其他任务。" />
              </h3>
            </div>
          </div>
          {detail.dataset_versions.length === 0 ? (
            <div className="workflow-next">
              <Empty text="先在导入页选择一张表用于建模，或完成多表关联。" />
              <Button onClick={() => setSection("upload")}>返回导入数据</Button>
            </div>
          ) : (
            <>
              <label>
                建模数据版本
                <Select
                  value={activeDatasetId}
                  disabled={Boolean(busy)}
                  onValueChange={(value) => {
                    setDatasetId(value);
                    setTargets([]);
                    setSelectedTasks(
                      detail.target_tasks
                        .filter(
                          (task) => task.dataset_version_id === value && task.status === "queued",
                        )
                        .map((task) => task.id),
                    );
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
                        disabled={Boolean(busy) || existingTargets.has(column)}
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
                      {existingTargets.has(column) ? "（已有任务）" : ""}
                    </label>
                  ))
                ) : (
                  <p>该版本没有识别到同时包含 0/1 的候选 Y。</p>
                )}
              </div>
              <Button disabled={!targets.length || Boolean(busy)} onClick={createTargets}>
                {busy === "targets"
                  ? "创建中…"
                  : targets.length
                    ? `创建 ${targets.length} 个目标任务`
                    : "先选择建模目标"}
              </Button>
            </>
          )}
          {currentTasks.length > 0 && (
            <div className="task-queue" ref={queueRef}>
              <div className="section-heading">
                <div>
                  <h3>
                    当前数据版本的建模任务
                    <Hint text="可一次启动多个任务，本地 Worker 按顺序执行。" />
                  </h3>
                </div>
              </div>
              {currentTasks.map((task) => (
                <label className="task-row" key={task.id}>
                  <Checkbox
                    disabled={
                      Boolean(busy) || !["queued", "failed", "blocked"].includes(task.status)
                    }
                    checked={selectedTaskIds.includes(task.id)}
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
              <p className="section-copy">
                {detail.project.mode === "semi_trusted"
                  ? "开始后会依次请你确认目标、样本、特征与模型方案。"
                  : "开始后，审核通过的步骤会自动继续。"}
              </p>
              <RunPreflight
                projectId={detail.project.id}
                taskIds={selectedTaskIds}
                objective={objective}
                onChange={setObjective}
                onReady={(ready) => setReadyKey(ready ? currentReadinessKey : "")}
              />
              <Button
                disabled={
                  !selectedTaskIds.length || Boolean(busy) || readyKey !== currentReadinessKey
                }
                onClick={startRuns}
              >
                {busy === "runs" ? "正在启动…" : `开始建模（${selectedTaskIds.length} 个目标）`}
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
