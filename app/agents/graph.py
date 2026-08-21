from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypedDict

from app.core.database import Database, new_id, now_iso
from app.core.paths import AppPaths, get_paths
from app.services.catalog import CatalogService
from app.services.pipeline import RunPipeline
from app.workers.process_runner import WorkerProcessRunner


class RunState(TypedDict, total=False):
    run_id: str
    project_id: str
    target_task_id: str
    mode: str
    halted: bool
    target: str
    target_evidence: dict[str, Any]
    target_gate: dict[str, Any]
    target_review: dict[str, Any]
    target_decision: dict[str, Any]
    profile: dict[str, Any]
    diagnostics: dict[str, Any]
    cleaning_plan: dict[str, Any]
    cleaning_result: dict[str, Any]
    data_gate: dict[str, Any]
    data_review: dict[str, Any]
    data_decision: dict[str, Any]
    working_dataset_version_id: str
    split_plan: dict[str, Any]
    split: dict[str, Any]
    split_gate: dict[str, Any]
    split_review: dict[str, Any]
    split_decision: dict[str, Any]
    screening: dict[str, Any]
    screening_gate: dict[str, Any]
    screening_review: dict[str, Any]
    screening_decision: dict[str, Any]
    binning: dict[str, Any]
    binning_gate: dict[str, Any]
    binning_review: dict[str, Any]
    binning_decision: dict[str, Any]
    model_plan: dict[str, Any]
    model_gate: dict[str, Any]
    model_plan_review: dict[str, Any]
    model_decision: dict[str, Any]
    model_result: dict[str, Any]
    report: dict[str, Any]
    report_review: dict[str, Any]
    execution_review: dict[str, Any]
    code_review: dict[str, Any]
    generated_code_path: str
    field_aliases: dict[str, str]
    effective_models: list[str]
    model_version_id: str
    package_manifest: dict[str, Any]
    worker_bundle_manifest_sha256: str
    artifact_ids: list[str]


TOOL_NODES = [
    (
        "prepare_target",
        "target_confirmation",
        "main_agent",
        "prepare_target",
        "已检查 Y 与有效样本",
    ),
    ("diagnose", "data_diagnosis", "main_agent", "diagnose_data", "已完成建模前诊断"),
    ("clean", "cleaning", "local_worker", "apply_cleaning", "已生成清洗数据版本"),
    ("propose_split", "split", "main_agent", "propose_split", "已提出样本切分方案"),
    ("execute_split", "split", "local_worker", "execute_split", "已完成 Train/Test/OOT 切分"),
    ("screen", "screening", "local_worker", "screen_features", "已完成 Train-only 变量筛选"),
    ("finalize_screen", "screening", "main_agent", "finalize_screening", "已冻结最终入模变量"),
    ("bin_features", "binning", "local_worker", "fit_binning", "已完成自动单调分箱"),
    ("finalize_binning", "binning", "main_agent", "finalize_binning", "已冻结分箱版本"),
    ("propose_models", "model_plan", "main_agent", "propose_models", "已提出候选模型与评分方案"),
    ("finalize_models", "model_plan", "main_agent", "finalize_model_plan", "已冻结建模方案"),
    (
        "code_review",
        "code_review",
        "reviewer_agent",
        "generate_and_review_code",
        "代码已完成独立质检",
    ),
    (
        "train_review",
        "training",
        "reviewer_agent",
        "train_and_review",
        "训练、校准与执行质检已完成",
    ),
    (
        "report_review",
        "reporting",
        "reviewer_agent",
        "build_and_review_report",
        "结构化报告已完成独立质检",
    ),
    (
        "write_artifacts",
        "reporting",
        "local_worker",
        "write_artifacts",
        "报告、模型包与评分入口已生成",
    ),
]

GATES = {
    "confirm_target": ("target_confirmation", "target_gate", "target_decision"),
    "confirm_data": ("data_diagnosis", "data_gate", "data_decision"),
    "confirm_split": ("split", "split_gate", "split_decision"),
    "confirm_screening": ("screening", "screening_gate", "screening_decision"),
    "confirm_binning": ("binning", "binning_gate", "binning_decision"),
    "confirm_models": ("model_plan", "model_gate", "model_decision"),
}


class RunEngine:
    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
        catalog: CatalogService | None = None,
        pipeline: RunPipeline | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.catalog = catalog or CatalogService(self.database, self.paths)
        self.pipeline = pipeline or RunPipeline(self.database, self.paths, self.catalog)
        self.worker = WorkerProcessRunner(self.paths)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="risk-model-worker")
        self._submit_lock = threading.RLock()
        self._checkpointer, self.persistence_mode = self._create_checkpointer()
        self.graph = self._build_graph()

    def _create_checkpointer(self) -> tuple[Any, str]:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            connection = sqlite3.connect(
                self.paths.root / "langgraph-checkpoints.sqlite3",
                check_same_thread=False,
            )
            saver = SqliteSaver(connection)
            if hasattr(saver, "setup"):
                saver.setup()
            self._checkpoint_connection = connection
            return saver, "sqlite"
        except ImportError:  # pragma: no cover - dependency fallback
            from langgraph.checkpoint.memory import InMemorySaver

            return InMemorySaver(), "memory-fallback"

    def _build_graph(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(RunState)
        for node, stage, agent, tool, summary in TOOL_NODES:
            builder.add_node(node, self._tool_node(node, stage, agent, tool, summary))
        for node, (stage, details_key, output_key) in GATES.items():
            builder.add_node(node, self._gate_node(node, stage, details_key, output_key))
        builder.add_node("complete", self._complete_node)
        builder.add_edge(START, "prepare_target")
        builder.add_edge("prepare_target", "confirm_target")
        self._conditional(builder, "confirm_target", "diagnose", END)
        builder.add_edge("diagnose", "confirm_data")
        self._conditional(builder, "confirm_data", "clean", END)
        builder.add_edge("clean", "propose_split")
        builder.add_edge("propose_split", "confirm_split")
        self._conditional(builder, "confirm_split", "execute_split", END)
        builder.add_edge("execute_split", "screen")
        builder.add_edge("screen", "confirm_screening")
        self._conditional(builder, "confirm_screening", "finalize_screen", END)
        builder.add_edge("finalize_screen", "bin_features")
        builder.add_edge("bin_features", "confirm_binning")
        self._conditional(builder, "confirm_binning", "finalize_binning", END)
        builder.add_edge("finalize_binning", "propose_models")
        builder.add_edge("propose_models", "confirm_models")
        self._conditional(builder, "confirm_models", "finalize_models", END)
        builder.add_edge("finalize_models", "code_review")
        builder.add_edge("code_review", "train_review")
        builder.add_edge("train_review", "report_review")
        builder.add_edge("report_review", "write_artifacts")
        builder.add_edge("write_artifacts", "complete")
        builder.add_edge("complete", END)
        return builder.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _conditional(builder: Any, node: str, next_node: str, end: Any) -> None:
        builder.add_conditional_edges(
            node,
            lambda state: "halt" if state.get("halted") else "continue",
            {"continue": next_node, "halt": end},
        )

    def create_run(
        self,
        project_id: str,
        target_task_id: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        project = self.catalog.get_project(project_id)
        if project["status"] == "archived":
            raise ValueError("PROJECT_ARCHIVED")
        task = self.catalog.require("target_tasks", target_task_id)
        if task["project_id"] != project_id:
            raise ValueError("CROSS_PROJECT_RUN_FORBIDDEN")
        selected_mode = mode or project["mode"]
        if selected_mode not in {"semi_trusted", "fully_trusted"}:
            raise ValueError("RUN_MODE_INVALID")
        identifier = new_id("run")
        timestamp = now_iso()
        state: RunState = {
            "run_id": identifier,
            "project_id": project_id,
            "target_task_id": target_task_id,
            "mode": selected_mode,
            "halted": False,
        }
        self.database.insert(
            "runs",
            {
                "id": identifier,
                "project_id": project_id,
                "target_task_id": target_task_id,
                "status": "queued",
                "stage": "project_setup",
                "node": "start",
                "mode": selected_mode,
                "seq": 0,
                "progress": 0,
                "state_json": state,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        self.database.update(
            "target_tasks", target_task_id, {"status": "queued", "updated_at": timestamp}
        )
        self.database.append_event(
            identifier,
            {
                "stage": "project_setup",
                "node": "start",
                "agent": "orchestrator",
                "tool": None,
                "status": "queued",
                "summary": "Run 已进入本地顺序队列",
                "evidence": {"target_task_id": target_task_id, "mode": selected_mode},
            },
        )
        self._submit(identifier, state)
        return self.catalog.require("runs", identifier)

    def resume(
        self, run_id: str, decision_id: str, approved: bool, edits: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        run = self.catalog.require("runs", run_id)
        decision = self.catalog.require("decisions", decision_id)
        if decision["run_id"] != run_id or decision["status"] != "pending":
            raise ValueError("DECISION_NOT_PENDING")
        if run["status"] != "awaiting_decision":
            raise ValueError("RUN_NOT_AWAITING_DECISION")
        from langgraph.types import Command

        response = {"decision_id": decision_id, "approved": approved, "edits": edits or {}}
        if not self.database.claim_decision(run_id, decision_id, response):
            raise ValueError("DECISION_NOT_PENDING")
        self._submit(run_id, Command(resume=response))
        return self.catalog.require("runs", run_id)

    def recover_incomplete(self) -> list[str]:
        recovered: list[str] = []
        for run in self.database.list("runs", limit=5000):
            if run["status"] not in {"queued", "running"}:
                continue
            state = run.get("state") or {
                "run_id": run["id"],
                "project_id": run["project_id"],
                "target_task_id": run["target_task_id"],
                "mode": run["mode"],
                "halted": False,
            }
            checkpoints = self.database.list("checkpoints", {"run_id": run["id"]}, limit=1)
            self._submit(run["id"], None if checkpoints else state)
            recovered.append(run["id"])
        return recovered

    def _submit(self, run_id: str, value: Any) -> None:
        with self._submit_lock:
            self._executor.submit(self._invoke, run_id, value)

    def _invoke(self, run_id: str, value: Any) -> None:
        config = {"configurable": {"thread_id": run_id}}
        try:
            result = self.graph.invoke(value, config=config)
            if isinstance(result, dict) and result.get("__interrupt__"):
                return
            if isinstance(result, dict) and result.get("halted"):
                return
            run = self.catalog.require("runs", run_id)
            if run["status"] not in {"succeeded", "blocked"}:
                self._mark_success(run_id, result if isinstance(result, dict) else {})
        except Exception as exc:
            self._mark_failure(run_id, exc)

    def _tool_node(
        self,
        node: str,
        stage: str,
        agent: str,
        tool: str,
        summary: str,
    ) -> Any:
        def execute(state: RunState) -> dict[str, Any]:
            run_id = state["run_id"]
            self.database.update(
                "runs",
                run_id,
                {
                    "status": "running",
                    "stage": stage,
                    "node": node,
                    "started_at": self.catalog.require("runs", run_id).get("started_at")
                    or now_iso(),
                    "updated_at": now_iso(),
                },
            )
            self.database.append_event(
                run_id,
                {
                    "stage": stage,
                    "node": node,
                    "agent": agent,
                    "tool": tool,
                    "status": "running",
                    "summary": f"正在执行：{summary}",
                    "evidence": {"tool_contract": tool},
                },
            )
            update = self.worker.invoke(tool, run_id, dict(state))
            merged = _jsonable({**state, **update})
            progress = (_node_position(node) + 1) / len(TOOL_NODES)
            event = self.database.append_event(
                run_id,
                {
                    "stage": stage,
                    "node": node,
                    "agent": agent,
                    "tool": tool,
                    "status": "completed",
                    "summary": summary,
                    "evidence": {"result_keys": sorted(update), "checkpoint": True},
                },
            )
            self.database.insert(
                "checkpoints",
                {
                    "id": new_id("chk"),
                    "run_id": run_id,
                    "node": node,
                    "seq": event["seq"],
                    "state_json": merged,
                    "created_at": now_iso(),
                },
            )
            self.database.update(
                "runs",
                run_id,
                {"state_json": merged, "progress": min(progress, 0.98), "updated_at": now_iso()},
            )
            return update

        return execute

    def _gate_node(self, node: str, stage: str, details_key: str, output_key: str) -> Any:
        def gate(state: RunState) -> dict[str, Any]:
            from langgraph.types import interrupt

            run_id = state["run_id"]
            details = state[details_key]
            existing = [
                item
                for item in self.database.list("decisions", {"run_id": run_id}, limit=500)
                if item["stage"] == stage
                and item["kind"] == node
                and item["status"] in {"pending", "submitted"}
            ]
            if existing:
                decision = existing[-1]
            else:
                decision = self.database.insert(
                    "decisions",
                    {
                        "id": new_id("decision"),
                        "run_id": run_id,
                        "stage": stage,
                        "kind": node,
                        "status": "pending",
                        "payload_json": details,
                        "review_json": _gate_review(state, stage),
                        "created_at": now_iso(),
                    },
                )
            if decision["status"] == "submitted":
                response = (decision.get("payload") or {}).get("response")
            elif state["mode"] == "fully_trusted":
                response = {
                    "decision_id": decision["id"],
                    "approved": True,
                    "edits": {},
                    "source": "fully_trusted_auto_approval",
                }
            else:
                if not existing:
                    self.database.append_event(
                        run_id,
                        {
                            "stage": stage,
                            "node": node,
                            "agent": "main_agent",
                            "tool": None,
                            "status": "awaiting_decision",
                            "summary": details["title"],
                            "evidence": {"decision_id": decision["id"], "review_completed": True},
                        },
                    )
                self.database.update(
                    "runs",
                    run_id,
                    {
                        "status": "awaiting_decision",
                        "stage": stage,
                        "node": node,
                        "updated_at": now_iso(),
                    },
                )
                response = interrupt(
                    {
                        "decision_id": decision["id"],
                        "stage": stage,
                        "title": details["title"],
                        "summary": details["summary"],
                        "editable": details.get("editable", []),
                    }
                )
            if not isinstance(response, dict) or response.get("decision_id") != decision["id"]:
                raise ValueError("DECISION_RESPONSE_INVALID")
            approved = bool(response.get("approved"))
            status = "approved" if approved else "rejected"
            self.database.update(
                "decisions",
                decision["id"],
                {
                    "status": status,
                    "payload_json": {**details, "response": response},
                    "resolved_at": now_iso(),
                },
            )
            if not approved:
                self.database.update(
                    "runs",
                    run_id,
                    {
                        "status": "blocked",
                        "stage": stage,
                        "node": node,
                        "updated_at": now_iso(),
                        "finished_at": now_iso(),
                    },
                )
                self.database.update(
                    "target_tasks",
                    state["target_task_id"],
                    {"status": "blocked", "updated_at": now_iso()},
                )
                self.database.append_event(
                    run_id,
                    {
                        "stage": stage,
                        "node": node,
                        "agent": "human",
                        "tool": None,
                        "status": "blocked",
                        "summary": "用户未批准当前阶段，Run 已安全停止",
                        "evidence": {"decision_id": decision["id"]},
                    },
                )
                return {output_key: response, "halted": True}
            self.database.append_event(
                run_id,
                {
                    "stage": stage,
                    "node": node,
                    "agent": "human" if state["mode"] == "semi_trusted" else "reviewer_agent",
                    "tool": None,
                    "status": "approved",
                    "summary": "阶段方案已确认",
                    "evidence": {"decision_id": decision["id"], "mode": state["mode"]},
                },
            )
            self.database.update("runs", run_id, {"status": "running", "updated_at": now_iso()})
            return {output_key: response, "halted": False}

        return gate

    def _complete_node(self, state: RunState) -> dict[str, Any]:
        self._mark_success(state["run_id"], dict(state))
        return {"halted": False}

    def _mark_success(self, run_id: str, state: dict[str, Any]) -> None:
        timestamp = now_iso()
        run = self.catalog.require("runs", run_id)
        merged = _jsonable({**(run.get("state") or {}), **state})
        self.database.update(
            "runs",
            run_id,
            {
                "status": "succeeded",
                "stage": "completed",
                "node": "complete",
                "progress": 1,
                "state_json": merged,
                "finished_at": timestamp,
                "updated_at": timestamp,
            },
        )
        if run.get("target_task_id"):
            self.database.update(
                "target_tasks",
                run["target_task_id"],
                {"status": "succeeded", "updated_at": timestamp},
            )
        self.database.append_event(
            run_id,
            {
                "stage": "completed",
                "node": "complete",
                "agent": "orchestrator",
                "tool": None,
                "status": "succeeded",
                "summary": "本 Y 任务已完成，可查看报告或批量评分",
                "evidence": {"model_version_id": merged.get("model_version_id")},
            },
        )

    def _mark_failure(self, run_id: str, error: Exception) -> None:
        timestamp = now_iso()
        try:
            run = self.catalog.require("runs", run_id)
        except KeyError:
            return
        code = str(error).split(":", 1)[0][:160] or type(error).__name__
        self.database.update(
            "runs",
            run_id,
            {"status": "failed", "error": code, "finished_at": timestamp, "updated_at": timestamp},
        )
        if run.get("target_task_id"):
            self.database.update(
                "target_tasks", run["target_task_id"], {"status": "failed", "updated_at": timestamp}
            )
        self.database.append_event(
            run_id,
            {
                "stage": run["stage"],
                "node": run["node"],
                "agent": "orchestrator",
                "tool": None,
                "status": "failed",
                "summary": "当前节点执行失败，其他 Y 任务不受影响",
                "evidence": {"error_code": code, "error_type": type(error).__name__},
            },
        )

    def shutdown(self) -> None:
        self.worker.shutdown()
        self._executor.shutdown(wait=False, cancel_futures=False)
        connection = getattr(self, "_checkpoint_connection", None)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _node_position(node: str) -> int:
    for index, item in enumerate(TOOL_NODES):
        if item[0] == node:
            return index
    return 0


def _gate_review(state: RunState, stage: str) -> dict[str, Any]:
    key = {
        "target_confirmation": "target_review",
        "data_diagnosis": "data_review",
        "split": "split_review",
        "screening": "screening_review",
        "binning": "binning_review",
        "model_plan": "model_plan_review",
    }.get(stage)
    value = state.get(key) if key else None  # type: ignore[literal-required]
    if isinstance(value, dict):
        return value
    return {"status": "pass", "evidence": {"deterministic_checks": True}}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)
