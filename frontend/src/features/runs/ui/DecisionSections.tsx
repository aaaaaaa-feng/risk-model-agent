import { useMemo, useState } from "react";
import { formatMetric, formatNumber, formatPercent } from "@/shared/lib/format";
import { monotonicLabel } from "../lib/labels";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Checkbox } from "@/shared/ui/checkbox";
import { Hint } from "@/shared/ui/hint";
import { Input } from "@/shared/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/shared/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui/table";
import { translateError } from "@/shared/lib/errors";
import { ManualBinningEditor } from "./ManualBinningEditor";
import type { ManualBinSpecError } from "../lib/binning";

const modelCatalog = [
  ["dummy", "Dummy", "效果下限与流程基线"],
  ["scorecard", "WOE Logistic Scorecard", "可解释评分卡与手动分箱"],
  ["regularized_logistic", "Regularized Logistic", "正则化线性挑战模型"],
  ["random_forest", "Random Forest", "袋装树模型"],
  ["extra_trees", "Extra Trees", "随机切分树模型"],
  ["xgboost", "XGBoost", "非线性效果模型"],
  ["lightgbm", "LightGBM", "资源允许时的挑战模型"],
  ["catboost", "CatBoost", "类别变量挑战模型"],
] as const;

interface RestoreFeature {
  column: string;
  reason: string;
}

export function TargetDecision({ summary }: { summary: import("../types").TargetSummary }) {
  const target = summary.target || summary;
  return (
    <div className="summary-grid four">
      <Metric
        label="有效样本"
        value={formatNumber(target.valid_count)}
        note={`排除 ${formatNumber((target.invalid_count || 0) + (target.missing_count || 0))}`}
      />
      <Metric label="好样本 0" value={formatNumber(target.negative_count)} />
      <Metric label="坏样本 1" value={formatNumber(target.positive_count)} />
      <Metric label="坏占比" value={formatPercent(target.bad_rate)} note="不使用 -1 / 空值" />
    </div>
  );
}

export function DataDecision({
  summary,
  edits,
  setEdits,
}: {
  summary: import("../types").DataSummary;
  edits: Record<string, unknown>;
  setEdits: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
}) {
  const accepted = (edits.accepted_action_ids as string[]) || [];
  return (
    <section className="decision-section">
      <h3>诊断与清洗动作</h3>
      {(summary.issues || []).length === 0 && <p className="success-line">没有数据质量阻断。</p>}
      <div className="issue-list">
        {(summary.issues || []).map((item, index) => (
          <div
            key={index}
            className={`issue ${item.severity}`}
            title={item.code ? `诊断码：${item.code}` : undefined}
          >
            <b>数据质量提示</b>
            <span>
              {
                translateError({ code: item.code, message: item.message }, { context: "review" })
                  .text
              }
            </span>
          </div>
        ))}
      </div>
      <div className="action-list">
        {(summary.actions || []).length ? (
          (summary.actions || []).map((action) => (
            <label key={action.id}>
              <Checkbox
                checked={accepted.includes(action.id)}
                onCheckedChange={(checked) =>
                  setEdits((current) => {
                    const currentAccepted = (current.accepted_action_ids as string[]) || [];
                    return {
                      ...current,
                      accepted_action_ids:
                        checked === true
                          ? [...currentAccepted, action.id]
                          : currentAccepted.filter((id) => id !== action.id),
                    };
                  })
                }
              />
              <span>
                <strong>{action.kind}</strong>
                <small>{action.columns?.join(", ") || "全表"}</small>
              </span>
            </label>
          ))
        ) : (
          <p>没有需要执行的清洗动作，将直接保留当前数据版本。</p>
        )}
      </div>
    </section>
  );
}

export function SplitDecision({
  summary,
  edits,
  setEdits,
}: {
  summary: import("../types").SplitSummary;
  edits: Record<string, unknown>;
  setEdits: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
}) {
  const plan = summary.plan || summary;
  const change = (key: string, value: unknown) =>
    setEdits((current) => ({ ...current, [key]: value }));
  return (
    <section className="decision-section">
      <div className="summary-grid four">
        <Metric label="方法" value={edits.method === "time_holdout" ? "时间 OOT" : "随机分层"} />
        <Metric label="时间字段" value={(edits.time_column as string | undefined) || "无"} />
        <Metric label="客户隔离" value={(edits.customer_key as string | undefined) || "未识别"} />
        <Metric label="随机种子" value={String(edits.random_state || 42)} />
      </div>
      <div className="form-grid">
        <label>
          切分方法
          <Select
            value={(edits.method as string | undefined) || plan.method || "time_holdout"}
            onValueChange={(value) => change("method", value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="time_holdout">时间 Train/Test/OOT</SelectItem>
              <SelectItem value="random_stratified">随机分层 Train/Test</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label>
          时间字段
          <Input
            value={(edits.time_column as string | undefined) || ""}
            onChange={(e) => change("time_column", e.target.value || null)}
            disabled={edits.method !== "time_holdout"}
          />
        </label>
        <label>
          客户主键
          <Input
            value={(edits.customer_key as string | undefined) || ""}
            onChange={(e) => change("customer_key", e.target.value || null)}
          />
        </label>
        <label>
          Test 比例
          <Input
            type="number"
            step="0.05"
            min="0.1"
            max="0.4"
            value={(edits.test_size as number | undefined) ?? 0.2}
            onChange={(e) => change("test_size", Number(e.target.value))}
          />
        </label>
        <label>
          OOT 比例
          <Input
            type="number"
            step="0.05"
            min="0.1"
            max="0.4"
            value={(edits.oot_size as number | undefined) ?? 0.2}
            onChange={(e) => change("oot_size", Number(e.target.value))}
            disabled={edits.method !== "time_holdout"}
          />
        </label>
      </div>
      <p className="boundary-note">
        同一客户不会跨数据集；OOT 锁定到最终报告，不参与调参和模型选择。
      </p>
    </section>
  );
}

export function ScreeningDecision({
  summary,
  edits,
  setEdits,
}: {
  summary: import("../types").ScreeningSummary;
  edits: Record<string, unknown>;
  setEdits: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const recoverable = (summary.excluded || []).filter((item) => item.recoverable);
  const selected = new Set(
    ((edits.restore_features as RestoreFeature[]) || []).map((item) => item.column),
  );
  const toggle = (item: import("../types").ScreeningExcluded, checked: boolean) => {
    const current = (edits.restore_features as RestoreFeature[]) || [];
    const reason = (reasons[item.column] || "").trim();
    setEdits({
      ...edits,
      restore_features: checked
        ? [...current, { column: item.column, reason }]
        : current.filter((v) => v.column !== item.column),
    });
  };
  return (
    <section className="decision-section">
      <div className="summary-grid four">
        <Metric label="最终入模" value={formatNumber(summary.included?.length)} />
        <Metric label="最低 IV" value={String(summary.thresholds?.iv ?? 0.02)} />
        <Metric label="最大缺失率" value={formatPercent(summary.thresholds?.missing_rate ?? 0.3)} />
        <Metric label="最大相关系数" value={String(summary.thresholds?.correlation ?? 0.7)} />
      </div>
      <h3>可恢复的排除变量</h3>
      <p className="section-copy">
        PII、泄漏、贷后不可用字段和主键不可恢复；普通变量需先填写至少 8
        个字符的业务理由，再勾选恢复。
      </p>
      <div className="table-wrap compact-table">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>恢复</TableHead>
              <TableHead>变量</TableHead>
              <TableHead>原因</TableHead>
              <TableHead>缺失率</TableHead>
              <TableHead>IV</TableHead>
              <TableHead>业务理由</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {recoverable.slice(0, 200).map((item) => (
              <TableRow key={item.column}>
                <TableCell>
                  <Checkbox
                    checked={selected.has(item.column)}
                    disabled={(reasons[item.column] || "").trim().length < 8}
                    title="请先填写至少 8 个字符的业务理由"
                    onCheckedChange={(checked) => toggle(item, checked === true)}
                  />
                </TableCell>
                <TableCell>{item.column}</TableCell>
                <TableCell>{item.reason}</TableCell>
                <TableCell>{formatPercent(item.missing_rate)}</TableCell>
                <TableCell>{formatMetric(item.iv)}</TableCell>
                <TableCell>
                  <Input
                    value={reasons[item.column] || ""}
                    onChange={(e) => {
                      const value = e.target.value;
                      setReasons((v) => ({ ...v, [item.column]: value }));
                      if (selected.has(item.column)) {
                        const features = (edits.restore_features as RestoreFeature[]) || [];
                        setEdits({
                          ...edits,
                          restore_features:
                            value.trim().length >= 8
                              ? features.map((v) =>
                                  v.column === item.column ? { ...v, reason: value.trim() } : v,
                                )
                              : features.filter((v) => v.column !== item.column),
                        });
                      }
                    }}
                    placeholder="至少 8 个字符"
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {recoverable.length === 0 && <p className="success-line">没有可恢复的排除变量。</p>}
    </section>
  );
}

export function BinningDecision({
  summary,
  manualColumn,
  dirtyColumns,
  setManualColumn,
  manualSpec,
  onManualSpecChange,
  manualSpecError,
  onManualVisualErrorChange,
}: {
  summary: import("../types").BinningSummary;
  manualColumn: string;
  dirtyColumns: string[];
  setManualColumn: (value: string) => void;
  manualSpec: string;
  onManualSpecChange: (value: string) => void;
  manualSpecError?: ManualBinSpecError | null;
  onManualVisualErrorChange: (error: string | null) => void;
}) {
  const specs = summary.specs || {};
  const columns = Object.keys(specs);
  const sample = manualColumn ? specs[manualColumn] : undefined;
  const stats = useMemo(() => (sample ? binStats(sample) : null), [sample]);
  const load = (column: string) => setManualColumn(column);
  return (
    <section className="decision-section">
      <div className="summary-grid three">
        <Metric label="分箱版本" value={summary.version || "—"} />
        <Metric label="入模变量" value={formatNumber(columns.length)} />
        <Metric
          label="未绝对单调"
          value={formatNumber(summary.non_monotonic?.length || 0)}
          note="可人工调整"
        />
      </div>
      <p className="section-copy">
        分箱只在 Train 样本拟合。左侧每个字段都能看到箱数、IV
        和单调状态；选择字段后，右侧展示完整的箱级样本、坏率、Lift、WOE、IV 和坏率趋势。
      </p>
      <div className="bin-layout">
        <div className="bin-list" aria-label="分箱字段列表">
          {columns.map((column) => {
            const spec = specs[column];
            const rows = spec.table || [];
            return (
              <button
                className={manualColumn === column ? "active" : ""}
                key={column}
                onClick={() => load(column)}
                title={`查看并调整 ${column} 的分箱结果`}
              >
                <strong>{column}</strong>
                <span>
                  {spec.kind} · {rows.length} 箱 · IV {formatMetric(spec.iv)}
                </span>
                <span className={spec.monotonic ? "bin-ok" : "bin-warn"}>
                  {spec.monotonic ? "单调" : "非单调，待调整"} ·{" "}
                  {spec.source === "manual" ? "人工" : "自动"}
                  {dirtyColumns.includes(column) ? " · 草稿已调整" : ""}
                </span>
              </button>
            );
          })}
        </div>
        <div className="bin-editor">
          {manualColumn && sample && stats ? (
            <>
              <div className="section-heading bin-detail-head">
                <div>
                  <h3>
                    {manualColumn} · 分箱结果
                    <Hint text="当前表格是已生成的分箱逻辑；如需调整，在下方编辑边界/类别组后确认。" />
                  </h3>
                </div>
                <Badge variant={sample.monotonic ? "ok" : "attention"} className="mt-[3px]">
                  {monotonicLabel(stats.rates, sample.monotonic)}
                </Badge>
              </div>
              <div className="summary-grid five bin-metric-grid">
                <Metric label="箱数" value={formatNumber(stats.rows.length)} />
                <Metric label="Train 样本" value={formatNumber(stats.count)} />
                <Metric label="坏占比" value={formatPercent(stats.overallRate)} />
                <Metric label="IV" value={formatMetric(sample.iv)} />
                <Metric
                  label="坏率范围"
                  value={`${formatPercent(stats.minRate)}-${formatPercent(stats.maxRate)}`}
                />
              </div>
              <div className="bin-ordering">
                <strong>单调性：</strong>
                <span>{monotonicLabel(stats.rates, sample.monotonic)}</span>
                <small>不把缺失箱参与趋势判断；缺失箱仍单独展示。</small>
              </div>
              <div className="table-wrap bin-table-wrap">
                <Table className="bin-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead>分箱</TableHead>
                      <TableHead>样本数</TableHead>
                      <TableHead>占比</TableHead>
                      <TableHead>好</TableHead>
                      <TableHead>坏</TableHead>
                      <TableHead>坏率 / 趋势</TableHead>
                      <TableHead>Lift</TableHead>
                      <TableHead>WOE</TableHead>
                      <TableHead>IV</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.rows.map((row, index) => {
                      const rate = Number(row.bad_rate);
                      const width = Number.isFinite(rate)
                        ? Math.max(
                            3,
                            Math.round((rate / Math.max(stats.maxRate || 0.01, 0.01)) * 100),
                          )
                        : 0;
                      const lift = stats.overallRate ? rate / stats.overallRate : null;
                      return (
                        <TableRow
                          key={`${row.bin}-${index}`}
                          className={row.bin === "<MISSING>" ? "bin-missing" : ""}
                        >
                          <TableCell>
                            <strong>{row.bin}</strong>
                          </TableCell>
                          <TableCell>{formatNumber(row.count)}</TableCell>
                          <TableCell>
                            {formatPercent(stats.count ? Number(row.count) / stats.count : null)}
                          </TableCell>
                          <TableCell>{formatNumber(row.good)}</TableCell>
                          <TableCell>{formatNumber(row.bad)}</TableCell>
                          <TableCell>
                            <div className="bin-rate-cell">
                              <span className="bin-rate-track">
                                <i style={{ width: `${width}%` }} />
                              </span>
                              <b>{formatPercent(row.bad_rate)}</b>
                            </div>
                          </TableCell>
                          <TableCell>{formatMetric(lift)}</TableCell>
                          <TableCell>{formatMetric(row.woe)}</TableCell>
                          <TableCell>{formatMetric(row.iv)}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
              <div className="bin-editor-grid">
                <div>
                  <h3>
                    人工分箱规则
                    <Hint text="保存后会生成新分箱版本，并使训练、质检和报告失效重跑。" />
                  </h3>
                  <ManualBinningEditor
                    key={manualColumn}
                    value={manualSpec}
                    onChange={onManualSpecChange}
                    submitError={manualSpecError}
                    onVisualErrorChange={onManualVisualErrorChange}
                  />
                </div>
                <div>
                  <h3>合并建议</h3>
                  {sample.merge_suggestions?.length ? (
                    <ul className="bin-merge-list">
                      {sample.merge_suggestions.slice(0, 8).map((item, index) => (
                        <li key={index}>
                          <b>
                            {item.left_bin} + {item.right_bin}
                          </b>
                          <span>
                            合并后坏率 {formatPercent(item.merged_bad_rate)} · 差异{" "}
                            {formatMetric(item.distance)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="success-line">当前没有需要优先合并的相邻箱。</p>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <span>OPTIONAL</span>
              <p>自动分箱已完成。请选择左侧变量查看完整结果。</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

interface BinStats {
  rows: import("../types").BinRow[];
  count: number;
  overallRate: number | null;
  rates: number[];
  minRate: number | null;
  maxRate: number | null;
}

function binStats(spec: import("../types").BinSpec): BinStats {
  const rows = Array.isArray(spec.table) ? spec.table : [];
  const count = rows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  const bad = rows.reduce((sum, row) => sum + Number(row.bad || 0), 0);
  const rates = rows
    .filter((row) => row.bin !== "<MISSING>" && Number.isFinite(Number(row.bad_rate)))
    .map((row) => Number(row.bad_rate));
  return {
    rows,
    count,
    overallRate: count ? bad / count : null,
    rates,
    minRate: rates.length ? Math.min(...rates) : null,
    maxRate: rates.length ? Math.max(...rates) : null,
  };
}

export function ModelDecision({
  plan,
  edits,
  setEdits,
}: {
  plan: import("../types").ModelPlan;
  edits: Record<string, unknown>;
  setEdits: React.Dispatch<React.SetStateAction<Record<string, unknown>>>;
}) {
  const selected = (edits.models as string[]) || [];
  const toggle = (name: string, checked: boolean) =>
    setEdits({
      ...edits,
      models: checked ? [...selected, name] : selected.filter((v) => v !== name),
    });
  const score = (edits.score as import("../types").ScoreConfig) || plan.score || {};
  const scoreChange = (key: string, value: number) =>
    setEdits({ ...edits, score: { ...score, [key]: value } });
  const budget = Number(edits.search_budget ?? plan.search_budget ?? 0);
  return (
    <section className="decision-section">
      <div className="section-heading">
        <div>
          <h3>
            候选模型执行矩阵
            <Hint text="资源预算只顺序运行推荐组合，不会默认全部跑。" />
          </h3>
        </div>
        <Button
          variant="link"
          onClick={() =>
            setEdits({ ...edits, models: plan.models, search_budget: plan.search_budget ?? 0 })
          }
        >
          恢复 Agent 推荐
        </Button>
      </div>
      <div className="model-grid head">
        <span>运行</span>
        <span>候选模型</span>
        <span>定位</span>
        <span>本次用途</span>
      </div>
      {modelCatalog.map(([id, name, purpose]) => (
        <label className="model-grid row" key={id}>
          <span>
            <Checkbox
              checked={selected.includes(id)}
              onCheckedChange={(checked) => toggle(id, checked === true)}
            />
          </span>
          <span>
            <strong>{name}</strong>
            <small>{id}</small>
          </span>
          <span>
            <i className={plan.models?.includes(id) ? "recommended" : "optional"}>
              {plan.models?.includes(id) ? "推荐" : "可选"}
            </i>
          </span>
          <span>{purpose}</span>
        </label>
      ))}
      <h3 className="score-title">评分转换</h3>
      <div className="form-grid score-fields">
        <label>
          最低分
          <Input
            type="number"
            value={score.minimum ?? 300}
            onChange={(e) => scoreChange("minimum", Number(e.target.value))}
          />
        </label>
        <label>
          最高分
          <Input
            type="number"
            value={score.maximum ?? 900}
            onChange={(e) => scoreChange("maximum", Number(e.target.value))}
          />
        </label>
        <label>
          基准分
          <Input
            type="number"
            value={score.base_score ?? 600}
            onChange={(e) => scoreChange("base_score", Number(e.target.value))}
          />
        </label>
        <label>
          基准好坏比
          <Input
            type="number"
            value={score.base_odds ?? 20}
            onChange={(e) => scoreChange("base_odds", Number(e.target.value))}
          />
        </label>
        <label>
          PDO
          <Input
            type="number"
            value={score.pdo ?? 50}
            onChange={(e) => scoreChange("pdo", Number(e.target.value))}
          />
        </label>
        <label>
          调参试验数
          <Input
            type="number"
            min="0"
            max="12"
            value={budget}
            onChange={(e) =>
              setEdits({
                ...edits,
                search_budget: Math.max(0, Math.min(12, Number(e.target.value))),
              })
            }
          />
        </label>
      </div>
      <p className="boundary-note">
        默认不额外调参；设为 1-12 后只在 Train/CV 使用固定小网格，Test 仍只用于方案选择，OOT
        不参与调参。
      </p>
    </section>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="summary-cell">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}
