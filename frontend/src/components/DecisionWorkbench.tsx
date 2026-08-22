import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Decision, Run } from "../types";

interface Props {
  run: Run;
  decision: Decision;
  onResolved: () => void;
  notify: (message: string, error?: boolean) => void;
}

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

export function DecisionWorkbench({ run, decision, onResolved, notify }: Props) {
  const details = decision.payload || {};
  const summary = details.summary || {};
  const [busy, setBusy] = useState(false);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [manualColumn, setManualColumn] = useState("");
  const [manualSpec, setManualSpec] = useState("");
  useEffect(() => {
    if (decision.kind === "confirm_data") setEdits({ accepted_action_ids: (summary.actions || []).filter((item:any) => item.recommended).map((item:any) => item.id) });
    else if (decision.kind === "confirm_split") setEdits({ ...(summary.plan || summary) });
    else if (decision.kind === "confirm_models") setEdits({ models: summary.plan?.models || [], score: summary.plan?.score || {}, search_budget: summary.plan?.search_budget ?? 0 });
    else if (decision.kind === "confirm_binning") {
      const first = Object.keys(summary.specs || {})[0] || "";
      setManualColumn(first);
      setManualSpec(first ? JSON.stringify(editableBinSpec(summary.specs[first]), null, 2) : "");
    }
    else setEdits({});
    if (decision.kind !== "confirm_binning") { setManualColumn(""); setManualSpec(""); }
  }, [decision.id]);
  const review = summary.review || decision.review || {};
  const confirm = async (approved: boolean) => {
    setBusy(true);
    try {
      const payloadEdits = { ...edits };
      if (decision.kind === "confirm_binning" && manualColumn && manualSpec.trim()) {
        payloadEdits.manual_specs = { [manualColumn]: JSON.parse(manualSpec) };
      }
      await api.post(`/runs/${run.id}/decisions/${decision.id}`, { approved, edits: payloadEdits });
      notify(approved ? "已确认；将从当前 checkpoint 继续" : "Run 已按你的决定安全停止"); onResolved();
    } catch (error) { notify(error instanceof Error ? error.message : "提交失败", true); }
    finally { setBusy(false); }
  };
  return <div className="decision-workbench">
    <div className="stage-line"><div><span className="eyebrow">HUMAN IN THE LOOP · {decision.stage}</span><h2>{details.title || stageName(decision.stage)}</h2><p>Reviewer 已先完成审核；你只需确认业务选择，不需要阅读长代码。</p></div><div className="run-meta">RUN <b>{run.id.slice(-8)}</b><br />CHECKPOINT <b>{run.node}</b></div></div>
    <div className={`review-banner ${review.status || "pass"}`}><div><span>AI REVIEW</span><strong>{reviewLabel(review.status)}</strong></div><p>{review.issues?.length ? `${review.issues.length} 条意见；展开下方可查看。` : "没有发现逻辑或安全阻断。"}</p></div>
    {review.status === "fallback_pass" && <p className="inline-warning agent-fallback-note">本阶段没有调用外部 LLM：当前使用本地确定性 Reviewer。若要启用 LLM，请到「设置中心」打开“启用 LLM”，保存后重新创建 Run；本次 Run 不会回溯重试。</p>}
    {decision.kind === "confirm_target" && <TargetDecision summary={summary.target || summary} />}
    {decision.kind === "confirm_data" && <DataDecision summary={summary} edits={edits} setEdits={setEdits} />}
    {decision.kind === "confirm_split" && <SplitDecision plan={summary.plan || summary} edits={edits} setEdits={setEdits} />}
    {decision.kind === "confirm_screening" && <ScreeningDecision summary={summary} edits={edits} setEdits={setEdits} />}
    {decision.kind === "confirm_binning" && <BinningDecision summary={summary} manualColumn={manualColumn} setManualColumn={setManualColumn} manualSpec={manualSpec} setManualSpec={setManualSpec} />}
    {decision.kind === "confirm_models" && <ModelDecision plan={summary.plan || {}} edits={edits} setEdits={setEdits} />}
    <details className="review-details"><summary>查看 Reviewer 结论与证据</summary>{review.issues?.length ? <ul>{review.issues.map((issue:any,index:number) => <li key={index}><b>{issue.code || "REVIEW"}</b><span>{issue.message || JSON.stringify(issue)}</span></li>)}</ul> : <p>确定性检查和独立上下文 Reviewer 均未发现阻断。</p>}<pre>{JSON.stringify(review.evidence || {}, null, 2)}</pre></details>
    <div className="decision-actions"><button className="button secondary danger-outline" disabled={busy} onClick={() => confirm(false)}>不批准并停止本 Run</button><button className="button primary" disabled={busy} onClick={() => confirm(true)}>{busy ? "提交中…" : confirmLabel(decision.kind)}</button></div>
  </div>;
}

function TargetDecision({ summary }: { summary: any }) {
  return <div className="summary-grid four"><Metric label="有效样本" value={format(summary.valid_count)} note={`排除 ${format((summary.invalid_count || 0) + (summary.missing_count || 0))}`} /><Metric label="好样本 0" value={format(summary.negative_count)} /><Metric label="坏样本 1" value={format(summary.positive_count)} /><Metric label="坏占比" value={percent(summary.bad_rate)} note="不使用 -1 / 空值" /></div>;
}

function DataDecision({ summary, edits, setEdits }: any) {
  const accepted = edits.accepted_action_ids || [];
  return <section className="decision-section"><h3>诊断与清洗动作</h3>{(summary.issues || []).length === 0 && <p className="success-line">没有数据质量阻断。</p>}<div className="issue-list">{(summary.issues || []).map((item:any,index:number) => <div key={index} className={`issue ${item.severity}`}><b>{item.code}</b><span>{item.message}</span></div>)}</div><div className="action-list">{(summary.actions || []).length ? summary.actions.map((action:any) => <label key={action.id}><input type="checkbox" checked={accepted.includes(action.id)} onChange={e => setEdits((current:any) => ({ ...current, accepted_action_ids: e.target.checked ? [...accepted,action.id] : accepted.filter((id:string) => id !== action.id) }))} /><span><strong>{action.kind}</strong><small>{action.columns?.join(", ") || "全表"}</small></span></label>) : <p>没有需要执行的清洗动作，将直接保留当前数据版本。</p>}</div></section>;
}

function SplitDecision({ plan, edits, setEdits }: any) {
  const change = (key:string,value:any) => setEdits((current:any) => ({ ...current, [key]: value }));
  return <section className="decision-section"><div className="summary-grid four"><Metric label="方法" value={edits.method === "time_holdout" ? "时间 OOT" : "随机分层"} /><Metric label="时间字段" value={edits.time_column || "无"} /><Metric label="客户隔离" value={edits.customer_key || "未识别"} /><Metric label="随机种子" value={String(edits.random_state || 42)} /></div><div className="form-grid"><label>切分方法<select value={edits.method || plan.method} onChange={e => change("method",e.target.value)}><option value="time_holdout">时间 Train/Test/OOT</option><option value="random_stratified">随机分层 Train/Test</option></select></label><label>时间字段<input value={edits.time_column || ""} onChange={e => change("time_column",e.target.value || null)} disabled={edits.method !== "time_holdout"} /></label><label>客户主键<input value={edits.customer_key || ""} onChange={e => change("customer_key",e.target.value || null)} /></label><label>Test 比例<input type="number" step="0.05" min="0.1" max="0.4" value={edits.test_size ?? .2} onChange={e => change("test_size",Number(e.target.value))} /></label><label>OOT 比例<input type="number" step="0.05" min="0.1" max="0.4" value={edits.oot_size ?? .2} onChange={e => change("oot_size",Number(e.target.value))} disabled={edits.method !== "time_holdout"} /></label></div><p className="boundary-note">同一客户不会跨数据集；OOT 锁定到最终报告，不参与调参和模型选择。</p></section>;
}

function ScreeningDecision({ summary, edits, setEdits }: any) {
  const [reasons,setReasons] = useState<Record<string,string>>({});
  const recoverable = (summary.excluded || []).filter((item:any) => item.recoverable);
  const selected = new Set((edits.restore_features || []).map((item:any) => item.column));
  const toggle = (item:any,checked:boolean) => {
    const current = edits.restore_features || [];
    const reason = (reasons[item.column] || "").trim();
    setEdits({ ...edits, restore_features: checked ? [...current,{column:item.column,reason}] : current.filter((v:any)=>v.column!==item.column) });
  };
  return <section className="decision-section"><div className="summary-grid four"><Metric label="最终入模" value={format(summary.included?.length)} /><Metric label="最低 IV" value={String(summary.thresholds?.iv ?? .02)} /><Metric label="最大缺失率" value={percent(summary.thresholds?.missing_rate ?? .3)} /><Metric label="最大相关系数" value={String(summary.thresholds?.correlation ?? .7)} /></div><h3>可恢复的排除变量</h3><p className="section-copy">PII、泄漏、贷后不可用字段和主键不可恢复；普通变量需先填写至少 8 个字符的业务理由，再勾选恢复。</p><div className="table-wrap compact-table"><table><thead><tr><th>恢复</th><th>变量</th><th>原因</th><th>缺失率</th><th>IV</th><th>业务理由</th></tr></thead><tbody>{recoverable.slice(0,200).map((item:any)=><tr key={item.column}><td><input type="checkbox" checked={selected.has(item.column)} disabled={(reasons[item.column] || "").trim().length < 8} title="请先填写至少 8 个字符的业务理由" onChange={e=>toggle(item,e.target.checked)} /></td><td>{item.column}</td><td>{item.reason}</td><td>{percent(item.missing_rate)}</td><td>{metric(item.iv)}</td><td><input value={reasons[item.column] || ""} onChange={e=>{const value=e.target.value;setReasons(v=>({...v,[item.column]:value}));if(selected.has(item.column))setEdits({...edits,restore_features:value.trim().length>=8?(edits.restore_features||[]).map((v:any)=>v.column===item.column?{...v,reason:value.trim()}:v):(edits.restore_features||[]).filter((v:any)=>v.column!==item.column)});}} placeholder="至少 8 个字符" /></td></tr>)}</tbody></table></div>{recoverable.length===0&&<p className="success-line">没有可恢复的排除变量。</p>}</section>;
}

function BinningDecision({ summary, manualColumn, setManualColumn, manualSpec, setManualSpec }: any) {
  const specs = summary.specs || {};
  const columns = Object.keys(specs);
  const sample = specs[manualColumn];
  const stats = useMemo(() => binStats(sample), [sample]);
  const load = (column: string) => {
    setManualColumn(column);
    setManualSpec(JSON.stringify(editableBinSpec(specs[column]), null, 2));
  };
  return <section className="decision-section">
    <div className="summary-grid three">
      <Metric label="分箱版本" value={summary.version || "—"} />
      <Metric label="入模变量" value={format(columns.length)} />
      <Metric label="未绝对单调" value={format(summary.non_monotonic?.length || 0)} note="可人工调整" />
    </div>
    <p className="section-copy">分箱只在 Train 样本拟合。左侧每个字段都能看到箱数、IV 和单调状态；选择字段后，右侧展示完整的箱级样本、坏率、Lift、WOE、IV 和坏率趋势。</p>
    <div className="bin-layout">
      <div className="bin-list" aria-label="分箱字段列表">
        {columns.map(column => {
          const spec = specs[column];
          const rows = spec.table || [];
          return <button className={manualColumn === column ? "active" : ""} key={column} onClick={() => load(column)}>
            <strong>{column}</strong>
            <span>{spec.kind} · {rows.length} 箱 · IV {metric(spec.iv)}</span>
            <span className={spec.monotonic ? "bin-ok" : "bin-warn"}>{spec.monotonic ? "单调" : "非单调，待调整"} · {spec.source === "manual" ? "人工" : "自动"}</span>
          </button>;
        })}
      </div>
      <div className="bin-editor">
        {manualColumn ? <>
          <div className="section-heading bin-detail-head"><div><h3>{manualColumn} · 分箱结果</h3><p>当前表格是已生成的分箱逻辑；如需调整，在下方编辑边界/类别组后确认。</p></div><span className={`status ${sample?.monotonic ? "succeeded" : "blocked"}`}>{monotonicLabel(stats.rates, sample?.monotonic)}</span></div>
          <div className="summary-grid five bin-metric-grid">
            <Metric label="箱数" value={format(stats.rows.length)} />
            <Metric label="Train 样本" value={format(stats.count)} />
            <Metric label="坏占比" value={percent(stats.overallRate)} />
            <Metric label="IV" value={metric(sample?.iv)} />
            <Metric label="坏率范围" value={`${percent(stats.minRate)}—${percent(stats.maxRate)}`} />
          </div>
          <div className="bin-ordering"><strong>单调性：</strong><span>{monotonicLabel(stats.rates, sample?.monotonic)}</span><small>不把缺失箱参与趋势判断；缺失箱仍单独展示。</small></div>
          <div className="table-wrap bin-table-wrap"><table className="bin-table"><thead><tr><th>分箱</th><th>样本数</th><th>占比</th><th>好</th><th>坏</th><th>坏率 / 趋势</th><th>Lift</th><th>WOE</th><th>IV</th></tr></thead><tbody>{stats.rows.map((row:any,index:number) => {
            const rate = Number(row.bad_rate);
            const width = Number.isFinite(rate) ? Math.max(3, Math.round((rate / Math.max(stats.maxRate || 0.01, 0.01)) * 100)) : 0;
            const lift = stats.overallRate ? rate / stats.overallRate : null;
            return <tr key={`${row.bin}-${index}`} className={row.bin === "<MISSING>" ? "bin-missing" : ""}><td><strong>{row.bin}</strong></td><td>{format(row.count)}</td><td>{percent(stats.count ? Number(row.count) / stats.count : null)}</td><td>{format(row.good)}</td><td>{format(row.bad)}</td><td><div className="bin-rate-cell"><span className="bin-rate-track"><i style={{ width: `${width}%` }} /></span><b>{percent(row.bad_rate)}</b></div></td><td>{metric(lift)}</td><td>{metric(row.woe)}</td><td>{metric(row.iv)}</td></tr>;
          })}</tbody></table></div>
          <div className="bin-editor-grid"><div><h3>人工分箱规则</h3><p>保存后会生成新分箱版本，并使训练、质检和报告失效重跑。</p><textarea value={manualSpec} onChange={e => setManualSpec(e.target.value)} spellCheck={false} rows={10} /></div><div><h3>合并建议</h3>{sample?.merge_suggestions?.length ? <ul className="bin-merge-list">{sample.merge_suggestions.slice(0, 8).map((item:any, index:number) => <li key={index}><b>{item.left_bin} + {item.right_bin}</b><span>合并后坏率 {percent(item.merged_bad_rate)} · 差异 {metric(item.distance)}</span></li>)}</ul> : <p className="success-line">当前没有需要优先合并的相邻箱。</p>}</div></div>
        </> : <div className="empty-state"><span>OPTIONAL</span><p>自动分箱已完成。请选择左侧变量查看完整结果。</p></div>}
      </div>
    </div>
  </section>;
}

function editableBinSpec(spec: any) {
  return spec?.kind === "numeric"
    ? { kind: "numeric", edges: spec.edges || [] }
    : { kind: "categorical", groups: spec?.groups || [], rare_values: spec?.rare_values || [] };
}

function binStats(spec: any) {
  const rows = Array.isArray(spec?.table) ? spec.table : [];
  const count = rows.reduce((sum: number, row: any) => sum + Number(row.count || 0), 0);
  const bad = rows.reduce((sum: number, row: any) => sum + Number(row.bad || 0), 0);
  const rates = rows.filter((row: any) => row.bin !== "<MISSING>" && Number.isFinite(Number(row.bad_rate))).map((row: any) => Number(row.bad_rate));
  return { rows, count, overallRate: count ? bad / count : null, rates, minRate: rates.length ? Math.min(...rates) : null, maxRate: rates.length ? Math.max(...rates) : null };
}

function monotonicLabel(rates: number[], monotonic: boolean | undefined) {
  if (!monotonic) return "非单调（需要调整或业务例外）";
  if (rates.length < 3) return "单调（箱数较少）";
  const differences = rates.slice(1).map((value, index) => value - rates[index]);
  if (differences.every(value => value >= -1e-12)) return "单调递增（坏率）";
  if (differences.every(value => value <= 1e-12)) return "单调递减（坏率）";
  return "单调性标记与趋势不一致，请复核";
}

function ModelDecision({ plan, edits, setEdits }: any) {
  const selected:string[] = edits.models || [];
  const toggle=(name:string,checked:boolean)=>setEdits({...edits,models:checked?[...selected,name]:selected.filter(v=>v!==name)});
  const score=edits.score || plan.score || {}; const scoreChange=(key:string,value:number)=>setEdits({...edits,score:{...score,[key]:value}});
  const budget=Number(edits.search_budget ?? plan.search_budget ?? 0);
  return <section className="decision-section"><div className="section-heading"><div><h3>候选模型执行矩阵</h3><p>资源预算只顺序运行推荐组合，不会默认全部跑。</p></div><button className="text-button" onClick={()=>setEdits({...edits,models:plan.models,search_budget:plan.search_budget ?? 0})}>恢复 Agent 推荐</button></div><div className="model-grid head"><span>运行</span><span>候选模型</span><span>定位</span><span>本次用途</span></div>{modelCatalog.map(([id,name,purpose])=><label className="model-grid row" key={id}><span><input type="checkbox" checked={selected.includes(id)} onChange={e=>toggle(id,e.target.checked)} /></span><span><strong>{name}</strong><small>{id}</small></span><span><i className={plan.models?.includes(id)?"recommended":"optional"}>{plan.models?.includes(id)?"推荐":"可选"}</i></span><span>{purpose}</span></label>)}<h3 className="score-title">评分转换</h3><div className="form-grid score-fields"><label>最低分<input type="number" value={score.minimum ?? 300} onChange={e=>scoreChange("minimum",Number(e.target.value))}/></label><label>最高分<input type="number" value={score.maximum ?? 900} onChange={e=>scoreChange("maximum",Number(e.target.value))}/></label><label>基准分<input type="number" value={score.base_score ?? 600} onChange={e=>scoreChange("base_score",Number(e.target.value))}/></label><label>基准好坏比<input type="number" value={score.base_odds ?? 20} onChange={e=>scoreChange("base_odds",Number(e.target.value))}/></label><label>PDO<input type="number" value={score.pdo ?? 50} onChange={e=>scoreChange("pdo",Number(e.target.value))}/></label><label>调参试验数<input type="number" min="0" max="12" value={budget} onChange={e=>setEdits({...edits,search_budget:Math.max(0,Math.min(12,Number(e.target.value)))})}/></label></div><p className="boundary-note">默认不额外调参；设为 1—12 后只在 Train/CV 使用固定小网格，Test 仍只用于方案选择，OOT 不参与调参。</p></section>;
}

function Metric({label,value,note}:{label:string;value:string;note?:string}){return <div className="summary-cell"><span>{label}</span><strong>{value}</strong>{note&&<small>{note}</small>}</div>}
function stageName(stage:string){return ({target_confirmation:"Y 确认",data_diagnosis:"数据诊断与清洗",split:"样本切分",screening:"变量筛选",binning:"变量分箱",model_plan:"建模方案"} as Record<string,string>)[stage]||stage}
function reviewLabel(status?:string){return ({pass:"预审通过",deterministic_pass:"确定性规则通过",llm_reviewer_pass:"LLM Reviewer 通过",fallback_pass:"本地降级质检通过",conditional_pass:"有条件通过",revise:"建议调整",block:"发现阻断",blocked:"发现阻断"} as Record<string,string>)[status||""]||"已完成预审"}
function confirmLabel(kind:string){return ({confirm_target:"确认 Y 并继续诊断",confirm_data:"确认清洗并继续",confirm_split:"确认切分并执行",confirm_screening:"冻结变量并继续",confirm_binning:"冻结分箱并继续",confirm_models:"确认方案并开始训练"} as Record<string,string>)[kind]||"确认并继续"}
function format(value:any){return value==null?"—":Number(value).toLocaleString()}
function percent(value:any){return value==null?"—":`${(Number(value)*100).toFixed(2)}%`}
function metric(value:any){return value==null?"—":Number(value).toFixed(4)}
