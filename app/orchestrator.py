from __future__ import annotations

import json
import html
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

import pandas as pd
from langgraph.graph import END, StateGraph

from .agent import ProviderGateway, build_safe_evidence, generate_report_narrative, generate_reproducible_code, propose_plan, repair_generated_code, review_generated_code, review_plan
from .config import BASE_DIR, WORKER_TIMEOUT_SECONDS, load_config
from .storage import Store, dumps, sha256_file, store
from .tools import require_tool
from .worker import (
    build_cleaning_plan,
    profile_table,
    quality_analysis,
    read_table,
    select_features,
    split_frame,
    target_summary,
)


class GraphState(TypedDict, total=False):
    run_id: str
    project_id: str
    dataset_id: str
    mode: str
    confirmed: bool
    profile: Dict[str, Any]
    target: Dict[str, Any]
    plan: Dict[str, Any]
    plan_review: Dict[str, Any]
    safe_evidence: Dict[str, Any]
    quality: Dict[str, Any]
    cleaning: Dict[str, Any]
    split: Dict[str, Any]
    selection: Dict[str, Any]
    training: Dict[str, Any]
    code: str
    code_review: Dict[str, Any]
    report: Dict[str, Any]


class RunCancelled(Exception):
    """The user cancelled a run at a safe orchestration boundary."""


class RunPaused(Exception):
    """The user paused a run; persisted state remains resumable."""


class RunContext:
    def __init__(self, data_store: Store, state: GraphState):
        self.store = data_store
        self.state = state

    @property
    def run_id(self) -> str:
        return self.state["run_id"]

    def event(self, event_type: str, message: str, **payload: Any) -> None:
        self.ensure_active()
        node = payload.get("node", self.state.get("phase", "run"))
        tool = payload.get("tool")
        if tool:
            require_tool(str(tool), str(node))
        event_payload = {
            "node": payload.pop("node", self.state.get("phase", "run")),
            "status": payload.pop("status", "running"),
            "message": message,
            "actor": payload.pop("actor", "orchestrator"),
            **payload,
        }
        self.store.append_event(self.run_id, event_type, event_payload)

    def ensure_active(self) -> None:
        current = self.store.get_run(self.run_id)
        status = current.get("status") if current else None
        if status == "cancelled":
            raise RunCancelled("RUN_CANCELLED")
        if status == "paused":
            raise RunPaused("RUN_PAUSED")

    def save(self, state: GraphState, status: Optional[str] = None, phase: Optional[str] = None) -> None:
        current = self.store.get_run(self.run_id)
        if current and current.get("status") in {"cancelled", "paused"}:
            return
        clean = {key: value for key, value in state.items() if key not in {"profile_dataframe"}}
        self.store.update_run(self.run_id, status=status, phase=phase, state=clean)

    def gateway(self, purpose: str) -> ProviderGateway:
        config = load_config()
        return ProviderGateway(
            config=config,
            budget_guard=lambda requested: self.store.provider_budget_error(self.run_id, requested, config),
            usage_callback=lambda tokens, model: self.store.record_provider_usage(self.run_id, tokens, model, purpose),
            request_callback=lambda logged_purpose, payload, model: self.store.record_provider_request(self.run_id, logged_purpose, model, payload),
            purpose=purpose,
        )


def _report_html(report: Dict[str, Any]) -> str:
    def safe(value: Any) -> str:
        return html.escape(str(value))

    metrics = report.get("metrics", [])
    rows = "".join(
        f"<tr><td>{safe(item.get('name', ''))}</td><td>{safe(item.get('validation', {}).get('roc_auc', '-'))}</td><td>{safe(item.get('validation', {}).get('ks', '-'))}</td><td>{safe(item.get('status', ''))}</td></tr>"
        for item in metrics
    )
    stability = report.get("stability") or {}
    stability_rows = "".join(
        f"<tr><td>{safe(item.get('feature', ''))}</td><td>{safe((item.get('validation') or {}).get('psi', '-'))}</td><td>{safe((item.get('validation') or {}).get('review_flag', '-'))}</td><td>{safe((item.get('oot') or {}).get('psi', '-'))}</td><td>{safe((item.get('oot') or {}).get('review_flag', '-'))}</td></tr>"
        for item in stability.get("features", [])[:50]
    )
    champion_importance = (report.get("champion") or {}).get("feature_importance", [])
    importance_rows = "".join(f"<tr><td>{safe(item.get('feature', ''))}</td><td>{safe(item.get('importance', item.get('absolute_coefficient', '-')))}</td></tr>" for item in champion_importance[:20])
    calibration_rows = "".join(
        f"<tr><td>{safe(item.get('bucket'))}</td><td>{safe(item.get('row_count'))}</td><td>{safe(item.get('predicted_rate'))}</td><td>{safe(item.get('observed_rate'))}</td><td>{safe(item.get('absolute_gap'))}</td></tr>"
        for item in ((report.get("champion") or {}).get("validation") or {}).get("calibration", [])
    )
    baseline = report.get("baseline") or {}
    baseline_row = ""
    if baseline:
        baseline_metrics = baseline.get("validation", {})
        swap = baseline.get("swap_set", {}).get("groups", {})
        swap_note = "；".join(f"{key}: {value.get('count', 0)} 条" for key, value in swap.items()) if swap else "未计算 swap set"
        baseline_row = f"<tr><td>Baseline · {safe(baseline.get('score_column', '-'))}</td><td>{safe(baseline_metrics.get('roc_auc', '-'))}</td><td>{safe(baseline_metrics.get('ks', '-'))}</td><td>既有模型比较（{safe(swap_note)}）</td></tr>"
    reevaluation_rows = "".join(
        f"<tr><td>{safe(item.get('dataset_filename', item.get('dataset_id', '-')))}</td><td>{safe((item.get('metrics') or {}).get('roc_auc', '-'))}</td><td>{safe((item.get('metrics') or {}).get('ks', '-'))}</td><td>{safe((item.get('fixed_rate') or {}).get('bad_capture_rate', '-'))}</td><td>冻结阈值 · 仅新 OOT</td></tr>"
        for item in report.get("baseline_reevaluations", [])
    )
    experiment_note = "<p><strong>实验 Run：</strong>本结果由 what-if 方案派生，默认未审核，不覆盖正式 Run。</p>" if report.get("manifest", {}).get("run_kind") == "experiment" else ""
    manifest_text = safe(json.dumps(report.get('manifest',{}), ensure_ascii=False, indent=2))
    narrative_sections = report.get("narrative_sections") or {}
    narrative_html = "".join(
        f"<article class='narrative-section'><h3>{safe(item.get('title', ''))}</h3><p>{safe(item.get('text', ''))}</p><small>来源：{safe(item.get('source', ''))} · 证据：{safe(', '.join(item.get('evidence_refs', [])))}</small></article>"
        for item in narrative_sections.get("sections", [])
    )
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>风控建模报告</title>
    <style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:40px auto;color:#18332f}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #dce9e4;text-align:left}}.hero{{background:#eaf7f0;padding:24px;border-radius:18px}}.narrative-section{{padding:14px 16px;margin:10px 0;border:1px solid #dce9e4;border-radius:12px;background:#fbfefc}}.narrative-section h3{{margin:0 0 6px}}.narrative-section p{{line-height:1.7;margin:0 0 7px}}.narrative-section small{{color:#718682}}</style>
    <div class='hero'><h1>{safe(report.get('title','风控建模报告'))}</h1><p>{safe(report.get('narrative',''))}</p><p>事实边界：离线实验结果，不代表生产效果。</p>{experiment_note}</div>
    <h2>报告叙事与证据</h2>{narrative_html or '<p>暂无章节叙事。</p>'}
    <h2>模型比较</h2><table><thead><tr><th>模型</th><th>验证 ROC-AUC</th><th>验证 KS</th><th>状态</th></tr></thead><tbody>{rows}{baseline_row}</tbody></table>
    <h2>冠军解释与稳定性</h2><p>下表为训练分区拟合规则下的变量重要性；PSI 仅作稳定性复核提示，不参与 OOT 选择。</p><table><thead><tr><th>变量</th><th>重要性/绝对系数</th></tr></thead><tbody>{importance_rows or '<tr><td colspan="2">暂无解释结果</td></tr>'}</tbody></table>
    <h3>验证集校准</h3><table><thead><tr><th>分箱</th><th>样本</th><th>预测坏率</th><th>实际坏率</th><th>绝对差</th></tr></thead><tbody>{calibration_rows or '<tr><td colspan="5">暂无校准结果</td></tr>'}</tbody></table>
    <table><thead><tr><th>变量</th><th>验证 PSI</th><th>验证提示</th><th>OOT PSI</th><th>OOT 提示</th></tr></thead><tbody>{stability_rows or '<tr><td colspan="5">暂无稳定性结果</td></tr>'}</tbody></table>
    <h2>探索与数据处理</h2><p>重复行：{report.get('quality', {}).get('duplicate_rows', 0)}；数值字段：{len(report.get('quality', {}).get('numeric', []))}；类别字段：{len(report.get('quality', {}).get('categorical', []))}</p><p>{report.get('cleaning', {}).get('note', '仅展示已记录的本地处理规则。')}</p><p>类别不平衡：训练分区 {safe((report.get('imbalance_policy') or {}).get('train_positive_count', '-'))} 个正类 / {safe((report.get('imbalance_policy') or {}).get('train_negative_count', '-'))} 个负类，采用算法级权重，不做重采样。</p>
    {f"<h2>既有模型基线</h2><p>分数列：{safe(baseline.get('score_column', '-'))}；方向：{safe(baseline.get('orientation', '-'))}；验证阈值在验证集冻结，OOT 仅评估。固定通过率：{safe(baseline.get('fixed_approval_rate', '-'))}；swap set 已按冠军与基线的验证排序生成聚合比较。</p>" if baseline else ""}
    {f"<h2>既有模型新 OOT 复评</h2><table><thead><tr><th>数据集</th><th>ROC-AUC</th><th>KS</th><th>固定通过率坏样本捕获</th><th>协议</th></tr></thead><tbody>{reevaluation_rows}</tbody></table>" if reevaluation_rows else ""}
    <h2>运行信息</h2><pre>{manifest_text}</pre></html>"""


def _write_report_xlsx(report: Dict[str, Any], path: Path) -> None:
    metrics = []
    lift_rows = []
    for item in report.get("metrics", []):
        validation = item.get("validation", {})
        train = item.get("train", {})
        oot = item.get("oot", {})
        oof = item.get("oof", {})
        metrics.append(
            {
                "model": item.get("name"),
                "status": item.get("status"),
                "features": item.get("features"),
                "validation_roc_auc": validation.get("roc_auc"),
                "validation_pr_auc": validation.get("pr_auc"),
                "validation_ks": validation.get("ks"),
                "validation_brier": validation.get("brier"),
                "validation_calibration_max_gap": max((row.get("absolute_gap", 0.0) for row in validation.get("calibration", [])), default=None),
                "oof_status": oof.get("status"),
                "oof_roc_auc": (oof.get("metrics") or {}).get("roc_auc"),
                "train_roc_auc": train.get("roc_auc"),
                "oot_roc_auc": oot.get("roc_auc"),
                "oot_ks": oot.get("ks"),
                "validation_bad_capture_at_fixed_rate": (item.get("validation_fixed_rate") or {}).get("bad_capture_rate"),
                "oot_bad_capture_at_fixed_rate": (item.get("oot_fixed_rate") or {}).get("bad_capture_rate"),
                "error": item.get("error"),
            }
        )
        for row in item.get("validation_lift", []):
            lift_rows.append({"model": item.get("name"), "scope": "validation", **row})
        for row in item.get("oot_lift", []):
            lift_rows.append({"model": item.get("name"), "scope": "oot", **row})
    selection = report.get("selection", {}).get("decisions", [])
    cleaning = report.get("cleaning", {})
    importance_rows = []
    for item in report.get("metrics", []):
        for row in item.get("feature_importance", []):
            importance_rows.append({"model": item.get("name"), **row})
    stability_rows = []
    for item in (report.get("stability") or {}).get("features", []):
        stability_rows.append({"feature": item.get("feature"), "validation_psi": (item.get("validation") or {}).get("psi"), "validation_review_flag": (item.get("validation") or {}).get("review_flag"), "oot_psi": (item.get("oot") or {}).get("psi"), "oot_review_flag": (item.get("oot") or {}).get("review_flag"), "fit_scope": "train"})
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{
            "title": report.get("title"),
            "run_id": report.get("manifest", {}).get("run_id"),
            "dataset_id": report.get("manifest", {}).get("dataset_id"),
            "champion": (report.get("champion") or {}).get("name"),
            "narrative": report.get("narrative"),
            "narrative_locked": (report.get("narrative_sections") or {}).get("locked"),
            "narrative_revision": (report.get("narrative_sections") or {}).get("revision", 0),
            "fact_boundary": "离线实验结果，不代表生产效果",
        }]).to_excel(writer, sheet_name="summary", index=False)
        pd.DataFrame(
            [
                {
                    "section_id": item.get("id"),
                    "title": item.get("title"),
                    "text": item.get("text"),
                    "source": item.get("source"),
                    "evidence_refs": ", ".join(item.get("evidence_refs", [])),
                }
                for item in (report.get("narrative_sections") or {}).get("sections", [])
            ]
        ).to_excel(writer, sheet_name="narrative", index=False)
        pd.DataFrame(metrics).to_excel(writer, sheet_name="metrics", index=False)
        pd.DataFrame(selection).to_excel(writer, sheet_name="selection", index=False)
        pd.DataFrame(lift_rows).to_excel(writer, sheet_name="lift", index=False)
        pd.DataFrame(cleaning.get("actions", [])).to_excel(writer, sheet_name="cleaning", index=False)
        pd.DataFrame((report.get("scorecard") or {}).get("points", [])).to_excel(writer, sheet_name="scorecard", index=False)
        pd.DataFrame(importance_rows).to_excel(writer, sheet_name="explanations", index=False)
        pd.DataFrame(stability_rows).to_excel(writer, sheet_name="stability", index=False)
        pd.DataFrame((report.get("scorecard") or {}).get("feature_importance", [])).to_excel(writer, sheet_name="scorecard_importance", index=False)
        pd.DataFrame([report.get("imbalance_policy") or {}]).to_excel(writer, sheet_name="imbalance", index=False)
        baseline = report.get("baseline") or {}
        if baseline:
            pd.DataFrame(
                [
                    {
                        "score_column": baseline.get("score_column"),
                        "orientation": baseline.get("orientation"),
                        "validation_roc_auc": (baseline.get("validation") or {}).get("roc_auc"),
                        "validation_ks": (baseline.get("validation") or {}).get("ks"),
                        "validation_pr_auc": (baseline.get("validation") or {}).get("pr_auc"),
                        "train_roc_auc": (baseline.get("train") or {}).get("roc_auc"),
                        "oot_roc_auc": (baseline.get("oot") or {}).get("roc_auc"),
                        "oot_ks": (baseline.get("oot") or {}).get("ks"),
                        "validation_bad_capture_at_fixed_rate": (baseline.get("validation_fixed_rate") or {}).get("bad_capture_rate"),
                        "oot_bad_capture_at_fixed_rate": (baseline.get("oot_fixed_rate") or {}).get("bad_capture_rate"),
                        "protocol": baseline.get("protocol"),
                        "oot_used_for_selection": baseline.get("oot_used_for_selection"),
                    }
                ]
            ).to_excel(writer, sheet_name="baseline", index=False)
            swap_rows = [{"group": key, **value} for key, value in (baseline.get("swap_set", {}).get("groups", {}) or {}).items()]
            pd.DataFrame(swap_rows).to_excel(writer, sheet_name="swap_set", index=False)
        reevaluations = []
        for item in report.get("baseline_reevaluations", []):
            metrics = item.get("metrics") or {}
            fixed_rate = item.get("fixed_rate") or {}
            reevaluations.append(
                {
                    "dataset_id": item.get("dataset_id"),
                    "dataset_filename": item.get("dataset_filename"),
                    "rows": item.get("rows"),
                    "score_column": item.get("score_column"),
                    "orientation": item.get("orientation"),
                    "threshold": item.get("threshold"),
                    "roc_auc": metrics.get("roc_auc"),
                    "ks": metrics.get("ks"),
                    "pr_auc": metrics.get("pr_auc"),
                    "bad_capture_rate": fixed_rate.get("bad_capture_rate"),
                    "eval_scope": item.get("eval_scope"),
                }
            )
        if reevaluations:
            pd.DataFrame(reevaluations).to_excel(writer, sheet_name="baseline_reevaluation", index=False)


def _write_checksums(run_dir: Path) -> Dict[str, str]:
    checksums = {
        str(path.relative_to(run_dir)): sha256_file(path)
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path.name != "checksums.json"
    }
    (run_dir / "checksums.json").write_text(dumps(checksums), encoding="utf-8")
    return checksums


def build_graph(context: RunContext, start: str = "profile"):
    graph = StateGraph(GraphState)
    graph.add_node("profile", lambda state: _node_profile(context, state))
    graph.add_node("plan", lambda state: _node_plan(context, state))
    graph.add_node("eda", lambda state: _node_eda(context, state))
    graph.add_node("cleaning", lambda state: _node_cleaning(context, state))
    graph.add_node("wait", lambda state: _node_wait(context, state))
    graph.add_node("blocked", lambda state: _node_blocked(context, state))
    graph.add_node("screen", lambda state: _node_screen(context, state))
    graph.add_node("train", lambda state: _node_train(context, state))
    graph.add_node("report", lambda state: _node_report(context, state))
    graph.set_entry_point(start)
    if start == "profile":
        graph.add_edge("profile", "plan")
    graph.add_conditional_edges("plan", _after_plan, {"wait": "wait", "blocked": "blocked", "eda": "eda"})
    graph.add_edge("eda", "cleaning")
    graph.add_conditional_edges("cleaning", _after_cleaning, {"wait": "wait", "screen": "screen"})
    graph.add_edge("screen", "train")
    graph.add_edge("train", "report")
    graph.add_edge("report", END)
    graph.add_edge("wait", END)
    graph.add_edge("blocked", END)
    return graph.compile()


def _after_plan(state: GraphState) -> str:
    if state.get("plan_review", {}).get("verdict") == "block":
        return "blocked"
    return "eda"


def _after_cleaning(state: GraphState) -> str:
    if state.get("mode") == "semi_trust" and not state.get("confirmed"):
        return "wait"
    return "screen"


def _node_profile(context: RunContext, state: GraphState) -> GraphState:
    context.state["phase"] = "profiling"
    context.event("node_started", "正在读取数据并建立本地画像", node="profiling", tool="profile_dataset")
    context.save(state, status="running", phase="profiling")
    dataset = context.store.get_dataset(state["dataset_id"])
    if not dataset:
        raise ValueError("DATASET_NOT_FOUND")
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    dictionary = context.store.latest_dictionary(state["project_id"])
    dictionary_metadata = (dictionary or {}).get("metadata") or {}
    profile = profile_table(frame, dictionary_metadata)
    target_name = profile.get("target_candidates", [None])[0]
    target = target_summary(frame, target_name) if target_name else {"target": None, "contract_ok": False}
    state.update(
        {
            "profile": profile,
            "target": target,
            "dictionary": {
                "id": (dictionary or {}).get("id"),
                "sha256": (dictionary or {}).get("sha256"),
                "field_count": dictionary_metadata.get("field_count", 0),
                "matched_count": profile.get("dictionary", {}).get("matched_count", 0),
                "schema_version": dictionary_metadata.get("schema_version"),
            },
        }
    )
    context.store.update_dataset_profile(state["dataset_id"], profile)
    context.event("node_progressed", f"画像完成：{profile['rows']:,} 行 / {profile['columns']:,} 个字段", node="profiling", tool="profile_dataset", progress=100, summary={"rows": profile["rows"], "columns": profile["columns"], "target_candidates": profile["target_candidates"]})
    context.save(state, status="running", phase="planning")
    return state


def _node_plan(context: RunContext, state: GraphState) -> GraphState:
    state["phase"] = "planning"
    context.event("agent_turn_started", "主 Agent 正在基于安全证据生成建模计划", node="planning", actor="main-agent")
    gateway = context.gateway("planning")
    plan = propose_plan(state["profile"], state["target"], state.get("mode", "auto"), gateway)
    evidence = build_safe_evidence(state["profile"], state["target"])
    review = review_plan(plan, state["profile"], state["target"], gateway=gateway)
    state.update({"plan": plan, "plan_review": review, "safe_evidence": evidence})
    context.event("agent_output_validated", "Reviewer 已完成计划审核", node="planning", actor="reviewer-agent", verdict=review["verdict"], findings=review["findings"], provider=gateway.status(), safe_evidence_summary={"fields": len(evidence.get("fields", [])), "raw_rows_included": False})
    if review["verdict"] == "block":
        context.save(state, status="blocked", phase="planning")
    else:
        context.save(state, status="running", phase="eda")
    return state


def _node_wait(context: RunContext, state: GraphState) -> GraphState:
    # The same graph terminal is used after planning and after cleaning. Keep
    # the latter phase visible instead of regressing the run back to planning.
    # ``phase`` is persisted in SQLite but intentionally is not a graph state
    # field, so use the presence of the cleaning plan as the durable marker.
    is_cleaning_gate = bool(state.get("cleaning"))
    node = "cleaning" if is_cleaning_gate else "planning"
    message = (
        "清洗方案已准备好，等待用户确认清洗提示、字段和模型集合"
        if is_cleaning_gate
        else "计划已准备好，等待用户确认 Y、切分、清洗提示、字段和模型集合"
    )
    phase = "cleaning" if is_cleaning_gate else "planning"
    context.event(
        "node_awaiting_confirmation",
        message,
        node=node,
        status="awaiting_confirmation",
        confirmation_required=["target", "split", "cleaning", "features", "models"],
    )
    context.save(state, status="awaiting_confirmation", phase=phase)
    return state


def _node_eda(context: RunContext, state: GraphState) -> GraphState:
    state["phase"] = "eda"
    context.event("node_started", "正在执行本地探索性分析和数据质量检查", node="eda", tool="quality_analysis")
    dataset = context.store.get_dataset(state["dataset_id"])
    if not dataset:
        raise ValueError("DATASET_NOT_FOUND")
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    target = state.get("plan", {}).get("target") or state.get("target", {}).get("target")
    time_column = state.get("plan", {}).get("time_column_suggestion")
    quality = quality_analysis(frame, target=target, time_column=time_column)
    state["quality"] = quality
    run_dir = context.store.run_dir(state["project_id"], state["run_id"])
    (run_dir / "eda.json").write_text(dumps(quality), encoding="utf-8")
    context.event(
        "tool_call_completed",
        f"探索分析完成：重复行 {quality.get('duplicate_rows', 0):,} 行，数值字段 {len(quality.get('numeric', []))} 个",
        node="eda",
        tool="quality_analysis",
        progress=100,
        summary={"rows": quality.get("rows"), "columns": quality.get("columns"), "duplicate_rows": quality.get("duplicate_rows")},
    )
    context.save(state, status="running", phase="cleaning")
    return state


def _node_cleaning(context: RunContext, state: GraphState) -> GraphState:
    state["phase"] = "cleaning"
    context.event("node_started", "正在生成可审计的数据清洗方案", node="cleaning", tool="build_cleaning_plan")
    dataset = context.store.get_dataset(state["dataset_id"])
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    cleaning = build_cleaning_plan(frame, state.get("profile", {}), state.get("quality", {}))
    state["cleaning"] = cleaning
    run_dir = context.store.run_dir(state["project_id"], state["run_id"])
    (run_dir / "cleaning-result.json").write_text(dumps(cleaning), encoding="utf-8")
    context.event(
        "tool_call_completed",
        "清洗方案已生成：安全标准化已应用，业务性删除/截断不会静默执行",
        node="cleaning",
        tool="build_cleaning_plan",
        progress=100,
        summary={"status": cleaning.get("status"), "requires_confirmation": len(cleaning.get("requires_confirmation", []))},
    )
    if state.get("mode") == "semi_trust" and not state.get("confirmed"):
        context.save(state, status="awaiting_confirmation", phase="cleaning")
    else:
        context.save(state, status="running", phase="screening")
    return state


def _node_blocked(context: RunContext, state: GraphState) -> GraphState:
    review = state.get("plan_review", {})
    context.event("node_blocked", "计划审核发现阻断问题，训练尚未启动", node="planning", status="blocked", findings=review.get("findings", []))
    context.save(state, status="blocked", phase="planning")
    return state


def _node_screen(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_started", "正在按训练集规则筛选变量", node="screening", tool="select_features")
    dataset = context.store.get_dataset(state["dataset_id"])
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    target = state["plan"]["target"]
    screening = state["plan"].get("screening", {})
    split_method = state.get("plan", {}).get("split", {}).get("method")
    time_column = state.get("plan", {}).get("time_column_suggestion") if split_method == "time_holdout" else None
    split = split_frame(frame, target, time_column)
    fit_positions = [split["positions"][index] for index in split["train"]]
    selection = select_features(
        frame,
        target,
        max_features=int(screening.get("max_features", 50)),
        min_iv=float(screening.get("min_iv", 0.005)),
        fit_positions=fit_positions,
        excluded_columns=screening.get("excluded_columns", []),
    )
    if not selection["selected"]:
        raise ValueError("NO_FEATURES_AFTER_SELECTION: 没有字段通过当前筛选规则")
    state["selection"] = selection
    state["split"] = split
    context.event("tool_call_completed", f"变量筛选完成：保留 {len(selection['selected'])} 个字段", node="screening", tool="select_features", progress=100, summary=selection["funnel"])
    context.save(state, status="running", phase="training")
    return state


def _node_train(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_started", "正在训练并比较候选模型", node="training", tool="train_candidate")
    dataset = context.store.get_dataset(state["dataset_id"])
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    target = state["plan"]["target"]
    split = state.get("split")
    if not split:
        split_method = state.get("plan", {}).get("split", {}).get("method")
        time_column = state["plan"].get("time_column_suggestion") if split_method == "time_holdout" else None
        split = split_frame(frame, target, time_column)
    output_dir = context.store.run_dir(state["project_id"], state["run_id"]) / "models"
    training = _run_training_worker(
        dataset_path=Path(dataset["path"]),
        sheet=dataset.get("sheet"),
        target=target,
        features=state["selection"]["selected"],
        split=split,
        output_dir=output_dir,
        models=state.get("plan", {}).get("models"),
        baseline_column=state.get("plan", {}).get("baseline_column"),
        should_stop=lambda: (context.store.get_run(state["run_id"]) or {}).get("status"),
    )
    state["training"] = training
    successful = len([item for item in training["candidates"] if item["status"] == "succeeded"])
    if successful == 0:
        raise ValueError("NO_MODEL_SUCCEEDED: 所有候选模型均训练失败，未生成成功报告")
    context.event("tool_call_completed", f"候选模型完成：{successful}/{len(training['candidates'])} 个成功", node="training", tool="train_candidate", progress=100, summary={"successful": successful, "champion": (training.get("champion") or {}).get("name")})
    context.save(state, status="running", phase="reporting")
    return state


def _run_training_worker(
    dataset_path: Path,
    sheet: Optional[str],
    target: str,
    features: list[str],
    split: Dict[str, Any],
    output_dir: Path,
    models: Optional[list[str]] = None,
    baseline_column: Optional[str] = None,
    should_stop: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run model fitting outside the Web process with a scrubbed environment."""
    payload = {
        "dataset_path": str(dataset_path),
        "sheet": sheet,
        "target": target,
        "features": features,
        "split": split,
        "output_dir": str(output_dir),
        "models": models,
        "baseline_column": baseline_column,
    }
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(BASE_DIR),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    for key in ("RISK_AGENT_MAX_UPLOAD_MB", "RISK_AGENT_MAX_ROWS", "RISK_AGENT_MAX_COLUMNS", "RISK_AGENT_MEMORY_BUDGET_MB"):
        if key in os.environ:
            env[key] = os.environ[key]
    command = [sys.executable, "-m", "app.worker_runner"]
    def stop_process() -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    # Use temporary files rather than PIPEs: a high-dimensional selection or
    # scorecard can exceed the OS pipe buffer, and the parent must remain able
    # to poll pause/cancel while the Worker is writing its JSON result.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            text=True,
        )
        process.stdin.write(json.dumps(payload, ensure_ascii=False))
        process.stdin.close()
        started = time.monotonic()
        while process.poll() is None:
            status = should_stop() if should_stop else None
            if status == "cancelled":
                stop_process()
                raise RunCancelled("RUN_CANCELLED")
            if status == "paused":
                stop_process()
                raise RunPaused("RUN_PAUSED")
            if time.monotonic() - started > WORKER_TIMEOUT_SECONDS:
                stop_process()
                raise RuntimeError(f"WORKER_TIMEOUT: 超过 {WORKER_TIMEOUT_SECONDS} 秒，训练进程已终止")
            time.sleep(0.1)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
    if process.returncode != 0:
        detail = (stderr or stdout or "worker exited without details").strip()[-1000:]
        raise RuntimeError(f"WORKER_FAILED: {detail}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WORKER_INVALID_RESULT: Worker 返回不是合法 JSON") from exc


def _node_report(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_started", "正在生成模型比较与专业报告", node="reporting", tool="render_report")
    training = state["training"]
    champion = training.get("champion") or {}
    run_kind = state.get("run_kind", "formal")
    title = "风控建模 Agent · what-if 实验报告" if run_kind == "experiment" else "风控建模 Agent · 离线模型报告"
    narrative_prefix = "这是从已完成 Run 派生的 what-if 实验，默认未审核；" if run_kind == "experiment" else "本次运行"
    report = {
        "title": title,
        "narrative": f"{narrative_prefix}完成了本地画像、训练集变量筛选和 {len(training.get('candidates', []))} 个候选模型比较。当前冠军建议为 {champion.get('name', '暂无')}，结论仅适用于本次冻结数据与验证协议。",
        "metrics": training.get("candidates", []),
        "champion": champion,
        "selection": state.get("selection", {}),
        "split": state.get("split", {}),
        "plan": state.get("plan", {}),
        "review": state.get("plan_review", {}),
        "code_review": {},
        "profile": state.get("profile", {}),
        "target": state.get("target", {}),
        "quality": state.get("quality", {}),
        "cleaning": (state.get("profile", {}) or {}).get("cleaning", {}),
        "selection_rule": {
            "primary": "validation_roc_auc",
            "tie_breaker": "validation_ks",
            "oot_locked_before_selection": True,
            "note": "V0.1 使用固定训练/验证/OOT 留出协议作候选排序，不将 OOT 结果用于调参；生产上线仍需独立模型审批。",
        },
        "scorecard": training.get("scorecard"),
        "stability": training.get("stability", {}),
        "baseline": training.get("baseline"),
        "imbalance_policy": training.get("imbalance_policy", {}),
        "manifest": {
            "run_id": state["run_id"],
            "dataset_id": state["dataset_id"],
            "run_kind": run_kind,
            "parent_run_id": state.get("parent_run_id"),
            "experimental_unreviewed": run_kind == "experiment",
            "protocol": "train_only_oof_diagnostic → validation_select → oot_once",
            "raw_data_uploaded": False,
            "oof_policy": f"3-fold when train rows <= {10000}; otherwise explicit skip",
        },
    }
    narrative_gateway = context.gateway("report_narrative")
    report["narrative_sections"] = generate_report_narrative(report, narrative_gateway)
    report["narrative"] = next(
        (
            item.get("text")
            for item in report["narrative_sections"].get("sections", [])
            if item.get("id") == "executive_summary"
        ),
        report.get("narrative", ""),
    )
    run_dir = context.store.run_dir(state["project_id"], state["run_id"])
    report["cleaning"] = state.get("cleaning", report["cleaning"])
    selected_features = state["selection"]["selected"]
    code = generate_reproducible_code(state["plan"], selected_features, state.get("profile"))
    review_history = []
    repair_history = []
    reviewer_gateway = context.gateway("code_review")
    for review_round in range(1, 4):
        context.ensure_active()
        (run_dir / f"generated_model_v{review_round}.py").write_text(code, encoding="utf-8")
        code_review = review_generated_code(code, gateway=reviewer_gateway, profile=state.get("profile"))
        code_review["review_round"] = review_round
        # Store a snapshot; appending the live dict would create a recursive
        # ``code_review.review_history -> code_review`` object later.
        review_history.append(dict(code_review))
        if code_review.get("verdict") != "block" or review_round == 3:
            break
        context.event(
            "revision_requested",
            f"Reviewer 第 {review_round} 轮发现阻断问题，主 Agent 将基于冻结方案重新生成代码",
            node="reporting",
            actor="main-agent",
            reviewer="reviewer-agent",
            review_round=review_round,
            findings=code_review.get("findings", []),
        )
        code, repair = repair_generated_code(code, state["plan"], selected_features, code_review.get("findings", []), state.get("profile"))
        repair["review_round"] = review_round
        repair_history.append(repair)
    code_review["review_history"] = review_history
    code_review["repair_history"] = repair_history
    code_review["max_review_rounds"] = 3
    context.ensure_active()
    (run_dir / "generated_model.py").write_text(code, encoding="utf-8")
    report["code_review"] = code_review
    report["provider_usage"] = context.store.provider_usage_totals(state["run_id"])
    artifact_names = sorted(
        str(path.relative_to(run_dir))
        for path in run_dir.rglob("*")
        if path.is_file() and path.name != "checksums.json"
    )
    artifact_names.extend(["report.json", "report.html", "report.xlsx"])
    report["manifest"]["artifacts"] = sorted(set(artifact_names))
    report["manifest"]["checksums_file"] = "checksums.json"
    _write_report_xlsx(report, run_dir / "report.xlsx")
    (run_dir / "report.json").write_text(dumps(report), encoding="utf-8")
    (run_dir / "report.html").write_text(_report_html(report), encoding="utf-8")
    _write_checksums(run_dir)
    state.update({"report": report, "code": code, "code_review": code_review})
    if code_review.get("verdict") == "block":
        context.event(
            "artifact_blocked",
            "生成代码未通过 Reviewer 静态安全检查，Run 已阻断，未将其标记为可交付成功结果",
            node="reporting",
            tool="review_generated_code",
            progress=100,
            status="blocked",
            findings=code_review.get("findings", []),
        )
        context.save(state, status="blocked", phase="reporting")
        return state
    context.event("artifact_validated", "报告与代码交付物已保存，生成代码未在产品内执行", node="reporting", tool="render_report", progress=100, status="succeeded", artifacts=report["manifest"].get("artifacts", []) + ["checksums.json"], code_review=code_review)
    context.save(state, status="succeeded", phase="reporting")
    if run_kind != "experiment":
        context.store.update_project_status(state["project_id"], "completed")
    return state


def run_graph(state: GraphState, start: str = "profile") -> None:
    context = RunContext(store, state)
    try:
        current = store.get_run(state["run_id"])
        if current and current.get("status") in {"cancelled", "paused"}:
            return
        start_phase = {"profile": "profiling", "eda": "eda", "screen": "screening"}.get(start, start)
        store.update_run(state["run_id"], status="running", phase=start_phase, state=state)
        context.event("run_started", "Run 已启动，本地 Worker 和 Agent 状态将持续写入事件流", node=start, status="running")
        graph = build_graph(context, start=start)
        result = graph.invoke(state)
        context.save(result, status=store.get_run(state["run_id"])["status"], phase=store.get_run(state["run_id"])["phase"])
    except (RunCancelled, RunPaused):
        # The API already persisted the terminal control state and event. Do
        # not overwrite it with a synthetic failure.
        return
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        current_phase = (store.get_run(state["run_id"]) or {}).get("phase", "unknown")
        if (store.get_run(state["run_id"]) or {}).get("status") in {"cancelled", "paused"}:
            return
        store.update_run(state["run_id"], status="failed", phase=current_phase, state=state, error=message)
        context.event("run_failed", "Run 失败，未把半成品当作成功结果", node=current_phase, status="failed", error=message)


EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="risk-agent")


def start_run(run: Dict[str, Any], initial_state: Optional[GraphState] = None, start: str = "profile") -> None:
    state: GraphState = {
        "run_id": run["id"],
        "project_id": run["project_id"],
        "dataset_id": run["dataset_id"],
        "mode": run["mode"],
        "confirmed": False,
    }
    if initial_state:
        state.update(initial_state)
    # Seeded experiment state may originate from a parent Run; the child
    # durable identity always wins over any copied metadata.
    state.update({"run_id": run["id"], "project_id": run["project_id"], "dataset_id": run["dataset_id"], "mode": run["mode"]})
    EXECUTOR.submit(run_graph, state, start)


def resume_after_confirmation(run: Dict[str, Any]) -> None:
    state = dict(run.get("state") or {})
    state["confirmed"] = True
    state["mode"] = run["mode"]
    EXECUTOR.submit(run_graph, state, "screen")


def resume_after_pause(run: Dict[str, Any]) -> None:
    state = dict(run.get("state") or {})
    state["mode"] = run["mode"]
    if not state.get("profile"):
        start = "profile"
    elif not state.get("plan"):
        start = "plan"
    elif not state.get("quality"):
        start = "eda"
    elif not state.get("cleaning"):
        start = "cleaning"
    elif not state.get("selection"):
        start = "screen"
    elif not state.get("training"):
        start = "train"
    else:
        start = "report"
    EXECUTOR.submit(run_graph, state, start)
