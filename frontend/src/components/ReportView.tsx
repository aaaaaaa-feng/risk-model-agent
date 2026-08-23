import { ChangeEvent, useEffect, useState } from "react";
import { api } from "../api";
import { errorMessage, formatMetric, formatPercent } from "../lib/format";
import type { Project, Run } from "../types";

interface Props {
  project: Project;
  run: Run | null;
  notify: (message: string, error?: boolean) => void;
}

interface ModelVersion {
  id: string;
  name: string;
}

interface ScoreJob {
  id: string;
  rows: number;
}

interface ReportData {
  schema_version: string;
  project: { name: string };
  target: { column: string };
  executive_summary?: Record<string, unknown>;
  champion?: Record<string, unknown>;
  sample_overview?: Record<string, unknown>;
  model_comparison?: Array<Record<string, unknown>>;
  feature_selection?: { selected?: Array<Record<string, unknown>> };
}

export function ReportView({ project, run, notify }: Props) {
  const [report, setReport] = useState<ReportData | null>(null);
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [modelId, setModelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [scoreJob, setScoreJob] = useState<ScoreJob | null>(null);

  useEffect(() => {
    setReport(null);
    setScoreJob(null);
    api
      .get<{ models: ModelVersion[] }>(`/projects/${project.id}/models`)
      .then((value) => {
        setModels(value.models);
        setModelId((current) => current || value.models[0]?.id || "");
      })
      .catch(() => undefined);
    if (run?.status === "succeeded")
      api
        .get<ReportData>(`/reports/${run.id}`)
        .then((value) => setReport(value))
        .catch((error) => notify(errorMessage(error), true));
  }, [project.id, run?.id, run?.status, notify]);

  const score = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !modelId) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", "score_input");
      const uploaded = await api.upload<{ asset: { id: string } }>(
        `/projects/${project.id}/data-assets`,
        form,
      );
      const result = await api.post<{ score_job: ScoreJob }>("/score-jobs", {
        model_version_id: modelId,
        input_asset_id: uploaded.asset.id,
      });
      setScoreJob(result.score_job);
      notify(`已为 ${result.score_job.rows.toLocaleString()} 条样本打分`);
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  };

  if (!run || run.status !== "succeeded")
    return (
      <div className="report-empty">
        <div className="stage-line">
          <div>
            <span className="eyebrow">REPORT</span>
            <h2>产物将在最终质检后生成</h2>
            <p>Web、Excel、单文件 HTML 和模型包共享同一结构化报告数据。</p>
          </div>
        </div>
        <div className="empty-state">
          <span>WAITING</span>
          <p>{run ? `当前 Run 状态：${run.status}` : "请选择或启动一个 Run。"}</p>
        </div>
      </div>
    );
  if (!report) return <div className="loading-panel">正在读取结构化模型报告…</div>;

  const summary = report.executive_summary || {};
  const champion = report.champion || {};
  const championMetrics = (name: string) =>
    (champion[`${name}_metrics`] as Record<string, unknown> | undefined) || {};

  return (
    <div className="report-view">
      <div className="stage-line">
        <div>
          <span className="eyebrow">RISK MODEL REPORT · {report.schema_version}</span>
          <h2>
            {report.project.name} · {report.target.column}
          </h2>
          <p>管理摘要与专业详情来自同一份事实数据。</p>
        </div>
        <div className="report-actions">
          <a className="button secondary" href={`/api/v1/reports/${run.id}/excel`}>
            导出 Excel
          </a>
          <a
            className="button secondary"
            target="_blank"
            rel="noreferrer"
            href={`/api/v1/reports/${run.id}/html`}
          >
            打开单页 HTML
          </a>
        </div>
      </div>
      <div className="summary-grid five">
        <Metric label="Champion" value={summary.champion} />
        <Metric label="Test AUC" value={formatMetric(championMetrics("test").roc_auc)} />
        <Metric label="Test KS" value={formatMetric(championMetrics("test").ks)} />
        <Metric label="OOT AUC" value={formatMetric(championMetrics("oot").roc_auc)} />
        <Metric label="质检结论" value={summary.quality_verdict === "pass" ? "通过" : "条件通过"} />
      </div>
      {summary.quality_verdict !== "pass" && (
        <div className="review-banner revise">
          <div>
            <span>QUALITY NOTICE</span>
            <strong>需关注排序</strong>
          </div>
          <p>
            {(summary.quality_notes as string[] | undefined)?.[0] ||
              "Test 等频分箱未达到绝对排序。"}
          </p>
        </div>
      )}
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>Train / Test / OOT 整体效果</h3>
            <p>样本量、好坏占比、AUC、KS、PR-AUC 与 PSI。</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>数据集</th>
                <th>样本量</th>
                <th>坏样本</th>
                <th>坏占比</th>
                <th>AUC</th>
                <th>KS</th>
                <th>PR-AUC</th>
                <th>PSI</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(report.sample_overview || {}).map(([name, value]) => {
                const overview = value as Record<string, unknown>;
                return (
                  <tr key={name}>
                    <td>
                      <strong>{name.toUpperCase()}</strong>
                    </td>
                    <td>{Number(overview.rows).toLocaleString()}</td>
                    <td>{Number(overview.positive_count).toLocaleString()}</td>
                    <td>{formatPercent(overview.bad_rate)}</td>
                    <td>{formatMetric(championMetrics(name).roc_auc)}</td>
                    <td>{formatMetric(championMetrics(name).ks)}</td>
                    <td>{formatMetric(championMetrics(name).pr_auc)}</td>
                    <td>
                      {formatMetric(
                        name === "test"
                          ? champion.train_test_score_psi
                          : name === "oot"
                            ? champion.test_oot_score_psi
                            : null,
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>候选模型对比</h3>
            <p>Test 选择，OOT 不参与候选排序。</p>
          </div>
        </div>
        <div className="candidate-bars">
          {(report.model_comparison || []).map((item) => {
            const candidate = item as Record<string, unknown>;
            const metrics = (candidate.test_metrics as Record<string, unknown>) || {};
            return (
              <div
                key={String(candidate.candidate)}
                className={candidate.candidate === summary.champion ? "champion" : ""}
              >
                <span>{String(candidate.candidate)}</span>
                <i
                  style={{
                    width: `${Math.max(0, Number(metrics.roc_auc || 0) * 100)}%`,
                  }}
                />
                <b>
                  {candidate.status === "trained"
                    ? `AUC ${formatMetric(metrics.roc_auc)} · KS ${formatMetric(metrics.ks)}`
                    : String(candidate.error_code ?? "—")}
                </b>
              </div>
            );
          })}
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>Champion 等频分箱</h3>
            <p>坏占比、Lift、累计捕获与绝对排序。</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>数据集</th>
                <th>箱</th>
                <th>样本量</th>
                <th>坏占比</th>
                <th>Lift</th>
                <th>累计捕获</th>
                <th>坏概率区间</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries((champion.lift as Record<string, unknown>) || {}).flatMap(
                ([name, rows]) =>
                  ((rows as Array<Record<string, unknown>>) || []).map((row) => (
                    <tr key={`${name}-${row.bucket}`}>
                      <td>{name.toUpperCase()}</td>
                      <td>{String(row.bucket)}</td>
                      <td>{Number(row.count).toLocaleString()}</td>
                      <td>{formatPercent(row.bad_rate)}</td>
                      <td>{formatMetric(row.lift)}</td>
                      <td>{formatPercent(row.cumulative_capture)}</td>
                      <td>
                        {formatMetric(row.min_probability)}—{formatMetric(row.max_probability)}
                      </td>
                    </tr>
                  )),
              )}
            </tbody>
          </table>
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>最终入模变量</h3>
            <p>报告只展示最终入模变量；完整排除原因保留在 Run 证据中。</p>
          </div>
          <span className="count-badge">{report.feature_selection?.selected?.length || 0}</span>
        </div>
        <div className="feature-pills">
          {(report.feature_selection?.selected || []).map((item) => {
            const feature = item as Record<string, unknown>;
            return (
              <span key={String(feature.column)}>
                <b>{String(feature.column)}</b>IV {formatMetric(feature.iv)}
              </span>
            );
          })}
        </div>
      </section>
      <section className="score-panel">
        <div>
          <span className="eyebrow">BATCH SCORING</span>
          <h3>给新样本批量打分</h3>
          <p>输出列使用模型名称命名，并同时保留原始分、坏概率与封顶/封底证据。</p>
        </div>
        <label>
          模型版本
          <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
            {models.map((model) => (
              <option value={model.id} key={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </label>
        <label className={`button primary file-button ${busy || !modelId ? "disabled" : ""}`}>
          {busy ? "评分中…" : "选择 CSV / Excel 并评分"}
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,.xls"
            disabled={busy || !modelId}
            onChange={score}
          />
        </label>
        {scoreJob && (
          <a className="button secondary" href={`/api/v1/score-jobs/${scoreJob.id}/download`}>
            下载 {scoreJob.rows.toLocaleString()} 行评分结果
          </a>
        )}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  const rendered =
    value == null
      ? "—"
      : typeof value === "string" || typeof value === "number"
        ? String(value)
        : "—";
  return (
    <div className="summary-cell">
      <span>{label}</span>
      <strong>{rendered}</strong>
    </div>
  );
}
