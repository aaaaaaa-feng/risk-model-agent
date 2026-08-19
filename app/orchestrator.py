from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

from langgraph.graph import END, StateGraph

from .agent import ProviderGateway, build_safe_evidence, generate_reproducible_code, propose_plan, review_generated_code, review_plan
from .config import BASE_DIR, WORKER_TIMEOUT_SECONDS
from .storage import Store, dumps, store
from .worker import profile_table, read_table, select_features, split_frame, target_summary


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
    selection: Dict[str, Any]
    training: Dict[str, Any]
    code: str
    code_review: Dict[str, Any]
    report: Dict[str, Any]


class RunContext:
    def __init__(self, data_store: Store, state: GraphState):
        self.store = data_store
        self.state = state

    @property
    def run_id(self) -> str:
        return self.state["run_id"]

    def event(self, event_type: str, message: str, **payload: Any) -> None:
        event_payload = {
            "node": payload.pop("node", self.state.get("phase", "run")),
            "status": payload.pop("status", "running"),
            "message": message,
            "actor": payload.pop("actor", "orchestrator"),
            **payload,
        }
        self.store.append_event(self.run_id, event_type, event_payload)

    def save(self, state: GraphState, status: Optional[str] = None, phase: Optional[str] = None) -> None:
        clean = {key: value for key, value in state.items() if key not in {"profile_dataframe"}}
        self.store.update_run(self.run_id, status=status, phase=phase, state=clean)


def _report_html(report: Dict[str, Any]) -> str:
    metrics = report.get("metrics", [])
    rows = "".join(
        f"<tr><td>{item.get('name')}</td><td>{item.get('validation', {}).get('roc_auc', '-')}</td><td>{item.get('validation', {}).get('ks', '-')}</td><td>{item.get('status')}</td></tr>"
        for item in metrics
    )
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>风控建模报告</title>
    <style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:40px auto;color:#18332f}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #dce9e4;text-align:left}}.hero{{background:#eaf7f0;padding:24px;border-radius:18px}}</style>
    <div class='hero'><h1>{report.get('title','风控建模报告')}</h1><p>{report.get('narrative','')}</p><p>事实边界：离线实验结果，不代表生产效果。</p></div>
    <h2>模型比较</h2><table><thead><tr><th>模型</th><th>验证 ROC-AUC</th><th>验证 KS</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>数据处理边界</h2><p>{report.get('cleaning', {}).get('note', '仅展示已记录的本地处理规则。')}</p>
    <h2>运行信息</h2><pre>{json.dumps(report.get('manifest',{}), ensure_ascii=False, indent=2)}</pre></html>"""


def build_graph(context: RunContext, start: str = "profile"):
    graph = StateGraph(GraphState)
    graph.add_node("profile", lambda state: _node_profile(context, state))
    graph.add_node("plan", lambda state: _node_plan(context, state))
    graph.add_node("wait", lambda state: _node_wait(context, state))
    graph.add_node("blocked", lambda state: _node_blocked(context, state))
    graph.add_node("screen", lambda state: _node_screen(context, state))
    graph.add_node("train", lambda state: _node_train(context, state))
    graph.add_node("report", lambda state: _node_report(context, state))
    graph.set_entry_point(start)
    if start == "profile":
        graph.add_edge("profile", "plan")
        graph.add_conditional_edges("plan", _after_plan, {"wait": "wait", "blocked": "blocked", "screen": "screen"})
    graph.add_edge("screen", "train")
    graph.add_edge("train", "report")
    graph.add_edge("report", END)
    graph.add_edge("wait", END)
    graph.add_edge("blocked", END)
    return graph.compile()


def _after_plan(state: GraphState) -> str:
    if state.get("plan_review", {}).get("verdict") == "block":
        return "blocked"
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
    profile = profile_table(frame)
    target_name = profile.get("target_candidates", [None])[0]
    target = target_summary(frame, target_name) if target_name else {"target": None, "contract_ok": False}
    state.update({"profile": profile, "target": target})
    context.store.update_dataset_profile(state["dataset_id"], profile)
    context.event("node_progressed", f"画像完成：{profile['rows']:,} 行 / {profile['columns']:,} 个字段", node="profiling", tool="profile_dataset", progress=100, summary={"rows": profile["rows"], "columns": profile["columns"], "target_candidates": profile["target_candidates"]})
    context.save(state, status="running", phase="planning")
    return state


def _node_plan(context: RunContext, state: GraphState) -> GraphState:
    state["phase"] = "planning"
    context.event("agent_turn_started", "主 Agent 正在基于安全证据生成建模计划", node="planning", actor="main-agent")
    gateway = ProviderGateway()
    plan = propose_plan(state["profile"], state["target"], state.get("mode", "auto"), gateway)
    evidence = build_safe_evidence(state["profile"], state["target"])
    review = review_plan(plan, state["profile"], state["target"])
    state.update({"plan": plan, "plan_review": review, "safe_evidence": evidence})
    context.event("agent_output_validated", "Reviewer 已完成计划审核", node="planning", actor="reviewer-agent", verdict=review["verdict"], findings=review["findings"], provider=gateway.status(), safe_evidence_summary={"fields": len(evidence.get("fields", [])), "raw_rows_included": False})
    if review["verdict"] == "block":
        context.save(state, status="blocked", phase="planning")
    elif state.get("mode") == "semi_trust" and not state.get("confirmed"):
        context.save(state, status="awaiting_confirmation", phase="planning")
    else:
        context.save(state, status="running", phase="screening")
    return state


def _node_wait(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_awaiting_confirmation", "计划已准备好，等待用户确认 Y、切分、字段和模型集合", node="planning", status="awaiting_confirmation", confirmation_required=["target", "split", "features", "models"])
    context.save(state, status="awaiting_confirmation", phase="planning")
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
    selection = select_features(frame, target, max_features=int(state["plan"]["screening"].get("max_features", 50)))
    state["selection"] = selection
    context.event("tool_call_completed", f"变量筛选完成：保留 {len(selection['selected'])} 个字段", node="screening", tool="select_features", progress=100, summary=selection["funnel"])
    context.save(state, status="running", phase="training")
    return state


def _node_train(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_started", "正在训练并比较候选模型", node="training", tool="train_candidate")
    dataset = context.store.get_dataset(state["dataset_id"])
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    target = state["plan"]["target"]
    split = split_frame(frame, target, state["plan"].get("time_column_suggestion"))
    output_dir = context.store.run_dir(state["project_id"], state["run_id"]) / "models"
    training = _run_training_worker(
        dataset_path=Path(dataset["path"]),
        sheet=dataset.get("sheet"),
        target=target,
        features=state["selection"]["selected"],
        split=split,
        output_dir=output_dir,
    )
    state["training"] = training
    successful = len([item for item in training["candidates"] if item["status"] == "succeeded"])
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
) -> Dict[str, Any]:
    """Run model fitting outside the Web process with a scrubbed environment."""
    payload = {
        "dataset_path": str(dataset_path),
        "sheet": sheet,
        "target": target,
        "features": features,
        "split": split,
        "output_dir": str(output_dir),
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
    for key in ("RISK_AGENT_MAX_UPLOAD_MB", "RISK_AGENT_MAX_ROWS", "RISK_AGENT_MAX_COLUMNS"):
        if key in os.environ:
            env[key] = os.environ[key]
    command = [sys.executable, "-m", "app.worker_runner"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            env=env,
            timeout=WORKER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"WORKER_TIMEOUT: 超过 {WORKER_TIMEOUT_SECONDS} 秒，训练进程已终止") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "worker exited without details").strip()[-1000:]
        raise RuntimeError(f"WORKER_FAILED: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WORKER_INVALID_RESULT: Worker 返回不是合法 JSON") from exc


def _node_report(context: RunContext, state: GraphState) -> GraphState:
    context.event("node_started", "正在生成模型比较与专业报告", node="reporting", tool="render_report")
    training = state["training"]
    champion = training.get("champion") or {}
    report = {
        "title": "风控建模 Agent · 离线模型报告",
        "narrative": f"本次运行完成了本地画像、训练集变量筛选和 {len(training.get('candidates', []))} 个候选模型比较。当前冠军建议为 {champion.get('name', '暂无')}，结论仅适用于本次冻结数据与验证协议。",
        "metrics": training.get("candidates", []),
        "champion": champion,
        "selection": state.get("selection", {}),
        "plan": state.get("plan", {}),
        "review": state.get("plan_review", {}),
        "code_review": {},
        "profile": state.get("profile", {}),
        "cleaning": (state.get("profile", {}) or {}).get("cleaning", {}),
        "selection_rule": {
            "primary": "validation_roc_auc",
            "tie_breaker": "validation_ks",
            "oot_locked_before_selection": True,
            "note": "V0.1 使用冻结规则作候选排序，不将 OOT 结果用于调参；生产上线仍需独立模型审批。",
        },
        "scorecard": training.get("scorecard"),
        "manifest": {"run_id": state["run_id"], "dataset_id": state["dataset_id"], "protocol": "train_cv → validation_select → oot_once", "raw_data_uploaded": False},
    }
    run_dir = context.store.run_dir(state["project_id"], state["run_id"])
    code = generate_reproducible_code(state["plan"], state["selection"]["selected"], state.get("profile"))
    (run_dir / "generated_model.py").write_text(code, encoding="utf-8")
    code_review = review_generated_code(code)
    report["code_review"] = code_review
    (run_dir / "report.json").write_text(dumps(report), encoding="utf-8")
    (run_dir / "report.html").write_text(_report_html(report), encoding="utf-8")
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
    context.event("artifact_validated", "报告与代码交付物已保存，生成代码未在产品内执行", node="reporting", tool="render_report", progress=100, status="succeeded", artifacts=["report.json", "report.html", "generated_model.py"], code_review=code_review)
    context.save(state, status="succeeded", phase="reporting")
    context.store.update_project_status(state["project_id"], "completed")
    return state


def run_graph(state: GraphState, start: str = "profile") -> None:
    context = RunContext(store, state)
    try:
        store.update_run(state["run_id"], status="running", phase="profiling" if start == "profile" else "screening", state=state)
        context.event("run_started", "Run 已启动，本地 Worker 和 Agent 状态将持续写入事件流", node=start, status="running")
        graph = build_graph(context, start=start)
        result = graph.invoke(state)
        context.save(result, status=store.get_run(state["run_id"])["status"], phase=store.get_run(state["run_id"])["phase"])
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        store.update_run(state["run_id"], status="failed", phase=state.get("phase", "unknown"), state=state, error=message)
        context.event("run_failed", "Run 失败，未把半成品当作成功结果", node=state.get("phase", "unknown"), status="failed", error=message)


EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="risk-agent")


def start_run(run: Dict[str, Any]) -> None:
    state: GraphState = {
        "run_id": run["id"],
        "project_id": run["project_id"],
        "dataset_id": run["dataset_id"],
        "mode": run["mode"],
        "confirmed": False,
    }
    EXECUTOR.submit(run_graph, state, "profile")


def resume_after_confirmation(run: Dict[str, Any]) -> None:
    state = dict(run.get("state") or {})
    state["confirmed"] = True
    state["mode"] = run["mode"]
    EXECUTOR.submit(run_graph, state, "screen")
