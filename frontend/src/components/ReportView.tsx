import { ChangeEvent, useEffect, useState } from "react";
import { api } from "../api";
import { errorMessage, formatMetric, formatPercent, isAbort } from "../lib/format";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { notify } from "@/lib/notify";
import { translateError, type FriendlyError } from "@/lib/errors";
import { statusLabel } from "@/lib/labels";
import { openDownloadedHtml, saveDownloadedFile } from "@/lib/download";
import { Hint } from "@/components/ui/hint";
import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Project, Run } from "../types";

interface Props {
  project: Project;
  run: Run | null;
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

export function ReportView({ project, run }: Props) {
  const [report, setReport] = useState<ReportData | null>(null);
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [modelId, setModelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [scoreJob, setScoreJob] = useState<ScoreJob | null>(null);
  const [reportError, setReportError] = useState<FriendlyError | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [exporting, setExporting] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setReport(null);
    setReportError(null);
    setScoreJob(null);
    setModels([]);
    setModelId("");
    api
      .get<{ models: ModelVersion[] }>(`/projects/${project.id}/models`, {
        signal: controller.signal,
      })
      .then((value) => {
        if (!active) return;
        setModels(value.models);
        setModelId(value.models[0]?.id || "");
      })
      .catch((error) => {
        if (active && !isAbort(error)) notify(errorMessage(error, { context: "model" }), true);
      });
    if (run?.status === "succeeded")
      api
        .get<ReportData>(`/reports/${run.id}`, { signal: controller.signal })
        .then((value) => {
          if (active) setReport(value);
        })
        .catch((error) => {
          if (!active || isAbort(error)) return;
          const friendly = translateError(error, { context: "model" });
          setReportError(friendly);
          notify(friendly.text, true);
        });
    return () => {
      active = false;
      controller.abort();
    };
  }, [project.id, reloadKey, run?.id, run?.status]);

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
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  };

  const downloadFile = async (path: string, fallbackName: string, busyKey: string) => {
    setExporting(busyKey);
    try {
      const file = await api.download(path);
      saveDownloadedFile(file, fallbackName);
    } catch (error) {
      notify(errorMessage(error), true);
    } finally {
      setExporting("");
    }
  };

  const openHtmlReport = async () => {
    const preview = window.open("about:blank", "_blank");
    if (!preview) {
      notify(errorMessage({ code: "REPORT_PREVIEW_POPUP_BLOCKED" }), true);
      return;
    }
    preview.document.title = "正在读取本地模型报告…";
    preview.document.body.textContent = "正在读取本地模型报告…";
    setExporting("html");
    try {
      const file = await api.download(`/reports/${run?.id}/html`);
      openDownloadedHtml(file, preview);
    } catch (error) {
      preview.close();
      notify(errorMessage(error, { context: "model" }), true);
    } finally {
      setExporting("");
    }
  };

  if (!run || run.status !== "succeeded")
    return (
      <div className="report-empty">
        <div className="stage-line">
          <div>
            <h2>
              产物将在最终质检后生成
              <Hint text="Web、Excel、单文件 HTML 和模型包共享同一结构化报告数据。" />
            </h2>
          </div>
        </div>
        <div className="empty-state">
          <span>等待报告</span>
          <p>{run ? `当前 Run 状态：${statusLabel(run.status)}` : "请选择或启动一个 Run。"}</p>
        </div>
      </div>
    );
  if (reportError)
    return (
      <div className="loading-panel" role="alert">
        <strong title={reportError.code ? `诊断码：${reportError.code}` : undefined}>
          {reportError.summary}
        </strong>
        {reportError.action && <p>{reportError.action}</p>}
        <Button variant="outline" onClick={() => setReloadKey((value) => value + 1)}>
          重新读取报告
        </Button>
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
          <h2>
            {report.project.name} · {report.target.column}
          </h2>
          <Hint text={`管理摘要与专业详情来自同一份事实数据 · Schema ${report.schema_version}。`} />
        </div>
        <div className="report-actions">
          <Button
            variant="outline"
            disabled={Boolean(exporting)}
            onClick={() =>
              downloadFile(`/reports/${run.id}/excel`, `${project.name}-模型报告.xlsx`, "excel")
            }
          >
            {exporting === "excel" ? "导出中…" : "导出 Excel"}
          </Button>
          <Button variant="outline" disabled={Boolean(exporting)} onClick={openHtmlReport}>
            {exporting === "html" ? "读取中…" : "打开单页 HTML"}
          </Button>
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
            <h3>
              Train / Test / OOT 整体效果
              <Hint text="样本量、好坏占比、AUC、KS、PR-AUC 与 PSI。" />
            </h3>
          </div>
        </div>
        <div className="table-wrap">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>数据集</TableHead>
                <TableHead>样本量</TableHead>
                <TableHead>坏样本</TableHead>
                <TableHead>坏占比</TableHead>
                <TableHead>AUC</TableHead>
                <TableHead>KS</TableHead>
                <TableHead>PR-AUC</TableHead>
                <TableHead>PSI</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries(report.sample_overview || {}).map(([name, value]) => {
                const overview = value as Record<string, unknown>;
                return (
                  <TableRow key={name}>
                    <TableCell>
                      <strong>{name.toUpperCase()}</strong>
                    </TableCell>
                    <TableCell>{Number(overview.rows).toLocaleString()}</TableCell>
                    <TableCell>{Number(overview.positive_count).toLocaleString()}</TableCell>
                    <TableCell>{formatPercent(overview.bad_rate)}</TableCell>
                    <TableCell>{formatMetric(championMetrics(name).roc_auc)}</TableCell>
                    <TableCell>{formatMetric(championMetrics(name).ks)}</TableCell>
                    <TableCell>{formatMetric(championMetrics(name).pr_auc)}</TableCell>
                    <TableCell>
                      {formatMetric(
                        name === "test"
                          ? champion.train_test_score_psi
                          : name === "oot"
                            ? champion.test_oot_score_psi
                            : null,
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>
              候选模型对比
              <Hint text="Test 选择，OOT 不参与候选排序。" />
            </h3>
          </div>
        </div>
        <div className="candidate-bars">
          {(report.model_comparison || []).map((item) => {
            const candidate = item as Record<string, unknown>;
            const metrics = (candidate.test_metrics as Record<string, unknown>) || {};
            const failure =
              candidate.status === "trained"
                ? null
                : translateError({ code: candidate.error_code }, { context: "model" });
            return (
              <div
                key={String(candidate.candidate)}
                className={candidate.candidate === summary.champion ? "champion" : ""}
                title={
                  failure
                    ? `${failure.action}${failure.code ? ` 诊断码：${failure.code}` : ""}`
                    : undefined
                }
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
                    : failure?.summary || "该候选模型未完成训练。"}
                </b>
              </div>
            );
          })}
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>
              Champion 等频分箱
              <Hint text="坏占比、Lift、累计捕获与绝对排序。" />
            </h3>
          </div>
        </div>
        <div className="table-wrap">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>数据集</TableHead>
                <TableHead>箱</TableHead>
                <TableHead>样本量</TableHead>
                <TableHead>坏占比</TableHead>
                <TableHead>Lift</TableHead>
                <TableHead>累计捕获</TableHead>
                <TableHead>坏概率区间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries((champion.lift as Record<string, unknown>) || {}).flatMap(
                ([name, rows]) =>
                  ((rows as Array<Record<string, unknown>>) || []).map((row) => (
                    <TableRow key={`${name}-${row.bucket}`}>
                      <TableCell>{name.toUpperCase()}</TableCell>
                      <TableCell>{String(row.bucket)}</TableCell>
                      <TableCell>{Number(row.count).toLocaleString()}</TableCell>
                      <TableCell>{formatPercent(row.bad_rate)}</TableCell>
                      <TableCell>{formatMetric(row.lift)}</TableCell>
                      <TableCell>{formatPercent(row.cumulative_capture)}</TableCell>
                      <TableCell>
                        {formatMetric(row.min_probability)}-{formatMetric(row.max_probability)}
                      </TableCell>
                    </TableRow>
                  )),
              )}
            </TableBody>
          </Table>
        </div>
      </section>
      <section className="report-section">
        <div className="section-heading">
          <div>
            <h3>
              最终入模变量
              <Hint text="报告只展示最终入模变量；完整排除原因保留在 Run 证据中。" />
            </h3>
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
          <h3>
            给新样本批量打分
            <Hint text="输出列使用模型名称命名，并同时保留原始分、坏概率与封顶/封底证据。" />
          </h3>
        </div>
        <label>
          模型版本
          <Select value={modelId} onValueChange={setModelId}>
            <SelectTrigger>
              <SelectValue placeholder="选择模型" />
            </SelectTrigger>
            <SelectContent>
              {models.map((model) => (
                <SelectItem value={model.id} key={model.id}>
                  {model.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label
          title="选择 CSV / Excel 文件并使用当前模型批量评分"
          className={cn(
            buttonVariants(),
            "file-button",
            (busy || !modelId) && "cursor-not-allowed opacity-50",
          )}
        >
          {busy ? "评分中…" : "选择 CSV / Excel 并评分"}
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,.xls"
            disabled={busy || !modelId}
            onChange={score}
          />
        </label>
        {scoreJob && (
          <Button
            variant="outline"
            disabled={Boolean(exporting)}
            onClick={() =>
              downloadFile(
                `/score-jobs/${scoreJob.id}/download`,
                `${project.name}-评分结果.csv`,
                "score",
              )
            }
          >
            {exporting === "score"
              ? "下载中…"
              : `下载 ${scoreJob.rows.toLocaleString()} 行评分结果`}
          </Button>
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
