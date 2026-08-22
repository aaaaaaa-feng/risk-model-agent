import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../lib/format";
import { Tabs } from "./ui/Tabs";
import type { DataAsset, ProjectDetail, RunCreatedResponse } from "../types";

interface Props {
  detail: ProjectDetail;
  onRefresh: () => Promise<void>;
  onRunsStarted: (runId: string) => void;
  notify: (message: string, error?: boolean) => void;
}

interface JoinStepDraft {
  id: string;
  right_asset_id: string;
  leftKeys: string;
  rightKeys: string;
}
type DataSection = "upload" | "join" | "target" | "notebook";
const dataSections: ReadonlyArray<readonly [DataSection, string]> = [
  ["upload", "1 导入"],
  ["join", "2 关联"],
  ["target", "3 Y 任务"],
  ["notebook", "Notebook"],
];

export function DataWorkbench({ detail, onRefresh, onRunsStarted, notify }: Props) {
  const [section, setSection] = useState<DataSection>("upload");
  const [uploadKind, setUploadKind] = useState("feature");
  const [busy, setBusy] = useState("");
  const [baseId, setBaseId] = useState("");
  const [steps, setSteps] = useState<JoinStepDraft[]>([]);
  const [recommendation, setRecommendation] = useState<unknown>(null);
  const [datasetId, setDatasetId] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [notebook, setNotebook] = useState<unknown>(null);
  const [document, setDocument] = useState<unknown>(null);
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
        await api.upload(`/projects/${detail.project.id}/data-assets`, form);
      }
      notify(`${files.length} 个文件已保存在本机`);
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
      await api.put(`/data-assets/${asset.id}/sheet`, { sheet });
      await onRefresh();
    } catch (error) {
      notify(errorMessage(error), true);
    }
  };
  const materialize = async (assetId: string) => {
    setBusy(assetId);
    try {
      await api.post(`/data-assets/${assetId}/materialize`);
      notify("已生成不可变数据版本");
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
      const result = await api.get<{
        recommendations?: Array<{ left_keys: string[]; right_keys: string[] }>;
      }>(
        `/join-plans/recommend?left_asset_id=${encodeURIComponent(baseId)}&right_asset_id=${encodeURIComponent(step.right_asset_id)}`,
      );
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
      const created = await api.post<{ join_plan: { id: string } }>("/join-plans", {
        project_id: detail.project.id,
        name: `关联方案 ${new Date().toLocaleTimeString()}`,
        base_asset_id: baseId,
        steps: payloadSteps,
      });
      await api.post(`/join-plans/${created.join_plan.id}/execute`, {
        target_columns: [],
        customer_key: null,
      });
      notify("关联完成；粒度、重复、膨胀和血缘检查已通过");
      await onRefresh();
      setSection("target");
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy("");
    }
  };
  const createNotebook = async (agentGenerated: boolean) => {
    const step = steps[0];
    setBusy("notebook");
    try {
      const result = await api.post<{ notebook: unknown; document: unknown }>("/notebooks", {
        project_id: detail.project.id,
        name: agentGenerated ? "Agent 关联草稿" : "手工关联 Notebook",
        template: agentGenerated ? "agent_join" : "blank",
        base_asset_id: agentGenerated ? baseId : undefined,
        right_asset_id: agentGenerated ? step?.right_asset_id : undefined,
        left_keys: agentGenerated ? splitKeys(step?.leftKeys || "") : [],
        right_keys: agentGenerated ? splitKeys(step?.rightKeys || "") : [],
      });
      setNotebook(result.notebook);
      setDocument(result.document);
      setSection("notebook");
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
      await api.post("/target-tasks", {
        project_id: detail.project.id,
        dataset_version_id: datasetId,
        target_columns: targets,
      });
      notify("多个 Y 已建立独立顺序任务");
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
        const result = await api.post<RunCreatedResponse>("/runs", {
          project_id: detail.project.id,
          target_task_id: taskId,
          mode: detail.project.mode,
        });
        first ||= result.run.id;
      }
      notify(`${selectedTasks.length} 个 Y 已进入本地顺序队列`);
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
          <span className="eyebrow">DATA PREPARATION</span>
          <h2>准备本地建模数据</h2>
          <p>支持直接建模、多表关联和 Notebook 兜底；每个结果都会重新校验。</p>
        </div>
        <div className="run-meta">
          PROJECT <b>{detail.project.id.slice(-8)}</b>
          <br />
          ASSETS <b>{detail.assets.length}</b>
        </div>
      </div>
      <Tabs
        aria-label="数据准备步骤"
        className="subnav"
        items={dataSections.map(([id, label]) => ({ id, label }))}
        value={section}
        onChange={(id) => setSection(id as DataSection)}
      />
      {section === "upload" && (
        <section
          id="data-panel-upload"
          className="work-section"
          role="tabpanel"
          aria-labelledby="tab-upload"
        >
          <div className="section-heading">
            <div>
              <h3>导入 CSV / Excel</h3>
              <p>可一次选择多张表；Excel 多 Sheet 会先要求选择 Sheet。</p>
            </div>
            <div className="upload-actions">
              <select
                aria-label="文件用途"
                value={uploadKind}
                onChange={(e) => setUploadKind(e.target.value)}
              >
                <option value="base">基准样本表</option>
                <option value="feature">特征表</option>
                <option value="dictionary">数据字典</option>
                <option value="score_input">待评分样本</option>
              </select>
              <label
                className={`button primary file-button ${busy === "upload" ? "disabled" : ""}`}
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
              <h3>四级关联工作流</h3>
              <p>Agent 推荐 → 可视化编辑 → Agent Notebook → 用户手写 Notebook。</p>
            </div>
            <button className="button secondary" onClick={addStep} disabled={assets.length < 2}>
              ＋ 添加特征表
            </button>
          </div>
          <label className="field-inline">
            基准表
            <select
              value={baseId}
              onChange={(e) => {
                setBaseId(e.target.value);
                setSteps([]);
              }}
            >
              <option value="">选择基准表</option>
              {assets
                .filter((a) => a.kind !== "dictionary")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
          <div className="join-steps">
            {steps.map((step, index) => (
              <div className="join-step" key={step.id}>
                <div className="join-step-index">{String(index + 1).padStart(2, "0")}</div>
                <label>
                  右表
                  <select
                    value={step.right_asset_id}
                    onChange={(e) =>
                      setSteps((current) =>
                        current.map((v) =>
                          v.id === step.id ? { ...v, right_asset_id: e.target.value } : v,
                        ),
                      )
                    }
                  >
                    <option value="">选择特征表</option>
                    {assets
                      .filter((a) => a.id !== baseId && a.kind !== "dictionary")
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  左键（逗号分隔）
                  <input
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
                  <input
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
                <button
                  className="button secondary"
                  onClick={() => recommend(step)}
                  disabled={busy === `recommend-${step.id}`}
                >
                  {busy === `recommend-${step.id}` ? "分析中…" : "Agent 推荐"}
                </button>
                <button
                  className="icon-button"
                  aria-label="删除步骤"
                  onClick={() => setSteps((current) => current.filter((v) => v.id !== step.id))}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {recommendation !== null && (
            <div className="review-strip">
              <strong>Agent 推荐</strong>
              <span>
                {(recommendation as { recommendations?: unknown[] }).recommendations?.length
                  ? `已按重合率和唯一性填入推荐键；仍需执行完整校验。`
                  : "没有可靠推荐，请使用可视化键或 Notebook。"}
              </span>
            </div>
          )}
          <div className="fallback-grid">
            <button
              className="fallback-card"
              onClick={executeJoin}
              disabled={!steps.length || busy === "join"}
            >
              <b>1—2</b>
              <strong>执行可视化关联</strong>
              <span>使用 Agent 推荐或手动编辑后的键</span>
            </button>
            <button
              className="fallback-card"
              onClick={() => createNotebook(true)}
              disabled={!steps.length}
            >
              <b>3</b>
              <strong>Agent 生成 Notebook</strong>
              <span>逐单元格核对并运行关联草稿</span>
            </button>
            <button className="fallback-card" onClick={() => createNotebook(false)}>
              <b>4</b>
              <strong>用户手写 Notebook</strong>
              <span>最末级兜底，不需要离开产品</span>
            </button>
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
              <h3>创建多个 Y 任务</h3>
              <p>-1 和空值会按每个 Y 独立排除；一个 Y 阻断不影响其他任务。</p>
            </div>
          </div>
          {detail.dataset_versions.length === 0 ? (
            <Empty text="请先把原始表物化，或完成多表关联。" />
          ) : (
            <>
              <label>
                建模数据版本
                <select
                  value={datasetId}
                  onChange={(e) => {
                    setDatasetId(e.target.value);
                    setTargets([]);
                  }}
                >
                  <option value="">选择版本</option>
                  {detail.dataset_versions.map((item) => (
                    <option value={item.id} key={item.id}>
                      {item.label} · {item.rows.toLocaleString()}×{item.columns}
                    </option>
                  ))}
                </select>
              </label>
              <div className="target-candidates">
                {binaryCandidates.length ? (
                  binaryCandidates.map((column) => (
                    <label key={column}>
                      <input
                        type="checkbox"
                        checked={targets.includes(column)}
                        onChange={(e) =>
                          setTargets((current) =>
                            e.target.checked
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
              <button
                className="button primary"
                disabled={!targets.length || busy === "targets"}
                onClick={createTargets}
              >
                {busy === "targets" ? "创建中…" : `创建 ${targets.length || ""} 个 Y 任务`}
              </button>
            </>
          )}
          {detail.target_tasks.length > 0 && (
            <div className="task-queue">
              <div className="section-heading">
                <div>
                  <h3>Y 任务队列</h3>
                  <p>可一次启动多个任务，本地 Worker 按顺序执行。</p>
                </div>
              </div>
              {detail.target_tasks.map((task) => (
                <label className="task-row" key={task.id}>
                  <input
                    type="checkbox"
                    disabled={!["queued", "failed", "blocked"].includes(task.status)}
                    checked={selectedTasks.includes(task.id)}
                    onChange={(e) =>
                      setSelectedTasks((current) =>
                        e.target.checked
                          ? [...current, task.id]
                          : current.filter((v) => v !== task.id),
                      )
                    }
                  />
                  <strong>{task.target_column}</strong>
                  <span>{task.valid_sample_count.toLocaleString()} 有效样本</span>
                  <em className={`status ${task.status}`}>{task.status}</em>
                </label>
              ))}
              <button
                className="button primary"
                disabled={!selectedTasks.length || busy === "runs"}
                onClick={startRuns}
              >
                {busy === "runs" ? "入队中…" : `启动 ${selectedTasks.length} 个 Run`}
              </button>
            </div>
          )}
        </section>
      )}
      {section === "notebook" && (
        <section
          id="data-panel-notebook"
          className="work-section"
          role="tabpanel"
          aria-labelledby="tab-notebook"
        >
          {notebook && document ? (
            <NotebookEditor
              notebook={notebook as { id: string; name: string; dataset_version_id?: string }}
              document={document as { cells: NotebookCell[] }}
              setDocument={setDocument as (value: { cells: NotebookCell[] }) => void}
              onRefresh={onRefresh}
              notify={notify}
            />
          ) : (
            <div className="notebook-empty">
              <h3>项目级本地 Notebook</h3>
              <p>
                用上一步的“Agent 生成 Notebook”或“用户手写
                Notebook”创建。代码可访问本机与网络，因此不是安全沙箱。
              </p>
              <button className="button secondary" onClick={() => createNotebook(false)}>
                创建空白 Notebook
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function AssetTable({
  assets,
  busy,
  onSheet,
  onMaterialize,
}: {
  assets: DataAsset[];
  busy: string;
  onSheet: (a: DataAsset, s: string) => void;
  onMaterialize: (id: string) => void;
}) {
  if (!assets.length) return <Empty text="尚未导入文件。原始文件不会上传到云端。" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>用途</th>
            <th>文件</th>
            <th>格式</th>
            <th>规模</th>
            <th>状态 / Sheet</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr key={asset.id}>
              <td>{asset.kind}</td>
              <td>
                <strong>{asset.name}</strong>
              </td>
              <td>{asset.format.toUpperCase()}</td>
              <td>
                {asset.rows == null
                  ? "待选择"
                  : `${asset.rows.toLocaleString()} × ${asset.columns}`}
              </td>
              <td>
                {asset.status === "sheet_selection_required" ? (
                  <select defaultValue="" onChange={(e) => onSheet(asset, e.target.value)}>
                    <option value="" disabled>
                      选择 Sheet
                    </option>
                    {asset.metadata?.sheets?.map((sheet) => (
                      <option key={sheet}>{sheet}</option>
                    ))}
                  </select>
                ) : (
                  <span className="status ready">ready</span>
                )}
              </td>
              <td>
                <button
                  className="text-button"
                  disabled={
                    asset.status !== "ready" || busy === asset.id || asset.kind === "dictionary"
                  }
                  onClick={() => onMaterialize(asset.id)}
                >
                  {busy === asset.id ? "生成中…" : "生成数据版本"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface NotebookEditorProps {
  notebook: { id: string; name: string; dataset_version_id?: string };
  document: { cells: NotebookCell[] };
  setDocument: (value: { cells: NotebookCell[] }) => void;
  onRefresh: () => Promise<void>;
  notify: (message: string, error?: boolean) => void;
}

interface NotebookCell {
  cell_type: string;
  source: string;
  execution_count?: number;
  outputs?: NotebookOutput[];
}

interface NotebookOutput {
  text?: string;
  evalue?: string;
  data?: Record<string, unknown>;
}

interface NotebookExecuteResponse {
  execution: {
    status: string;
    execution_count?: number;
    outputs?: NotebookOutput[];
  };
}

function NotebookEditor({ notebook, document, setDocument, onRefresh, notify }: NotebookEditorProps) {
  const [busy, setBusy] = useState("");
  const [output, setOutput] = useState("joined_output.csv");
  const [label, setLabel] = useState("Notebook 关联结果");
  const save = async () => {
    setBusy("save");
    try {
      await api.put(`/notebooks/${notebook.id}`, { notebook: document });
      notify("Notebook 已保存");
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  const execute = async (index: number) => {
    setBusy(`cell-${index}`);
    try {
      await save();
      const result = await api.post<NotebookExecuteResponse>(`/notebooks/${notebook.id}/execute-cell`, {
        cell_index: index,
      });
      const copy = structuredClone(document);
      copy.cells[index].outputs = result.execution.outputs;
      copy.cells[index].execution_count = result.execution.execution_count;
      setDocument(copy);
      notify(
        result.execution.status === "succeeded" ? "单元格执行完成" : "单元格执行失败",
        result.execution.status !== "succeeded",
      );
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  const importOutput = async () => {
    setBusy("import");
    try {
      await api.post(`/notebooks/${notebook.id}/dataset-versions`, {
        relative_path: output,
        label,
        parent_dataset_version_id: notebook.dataset_version_id || null,
        expected_grain: "same_or_fewer_rows",
      });
      notify("Notebook 输出已通过校验并生成数据版本");
      await onRefresh();
    } catch (e) {
      notify(errorMessage(e), true);
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="notebook-editor">
      <div className="section-heading">
        <div>
          <span className="eyebrow">LOCAL KERNEL</span>
          <h3>{notebook.name}</h3>
          <p>网络默认开启 · 用户代码不在安全沙箱中 · 产品不会主动外发原始数据</p>
        </div>
        <button className="button secondary" onClick={save}>
          {busy === "save" ? "保存中…" : "保存 Notebook"}
        </button>
      </div>
      {document.cells.map((cell, index) => (
        <div className={`nb-cell ${cell.cell_type}`} key={index}>
          <div className="nb-gutter">[{cell.execution_count ?? " "}]</div>
          {cell.cell_type === "code" ? (
            <>
              <textarea
                value={cell.source}
                onChange={(e) => {
                  const copy = structuredClone(document);
                  copy.cells[index].source = e.target.value;
                  setDocument(copy);
                }}
                spellCheck={false}
              />
              <button className="cell-run" onClick={() => execute(index)} disabled={Boolean(busy)}>
                ▶ 运行
              </button>
              {cell.outputs && cell.outputs.length > 0 && (
                <pre className="cell-output">
                  {cell.outputs
                    .map((item) => (item as NotebookOutput).text || (item as NotebookOutput).evalue || JSON.stringify((item as NotebookOutput).data || {}))
                    .join("\n")}
                </pre>
              )}
            </>
          ) : (
            <textarea
              value={cell.source}
              onChange={(e) => {
                const copy = structuredClone(document);
                copy.cells[index].source = e.target.value;
                setDocument(copy);
              }}
            />
          )}
        </div>
      ))}
      <div className="import-output">
        <label>
          输出文件
          <input value={output} onChange={(e) => setOutput(e.target.value)} />
        </label>
        <label>
          数据版本名称
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <button className="button primary" onClick={importOutput} disabled={busy === "import"}>
          {busy === "import" ? "校验中…" : "校验并生成数据版本"}
        </button>
      </div>
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
