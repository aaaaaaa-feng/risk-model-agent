from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import ProviderGateway
from .config import MAX_UPLOAD_BYTES, ensure_runtime, load_config, new_id, public_config, save_config
from .orchestrator import resume_after_confirmation, resume_after_pause, start_run
from .storage import sha256_file, store
from .tools import registry_manifest, require_tool
from .worker import apply_cleaning_plan, list_sheets, profile_table, quality_analysis, read_table, segment_analysis, target_summary

ensure_runtime()
app = FastAPI(title="风控建模 Agent", version=__version__)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RunCreate(BaseModel):
    dataset_id: str
    mode: str = Field(default="auto", pattern="^(auto|semi_trust)$")


class DecisionPayload(BaseModel):
    kind: str
    values: Dict[str, Any] = Field(default_factory=dict)


class FeedbackPayload(BaseModel):
    reaction: str = Field(pattern="^(like|dislike)$")
    reason: Optional[str] = Field(default=None, max_length=500)
    event_id: Optional[str] = None


class ConfigPayload(BaseModel):
    provider: str = "OpenAI-compatible"
    base_url: str = ""
    model: str = ""
    reviewer_model: str = ""
    llm_enabled: bool = False
    api_key: str = ""
    clear_api_key: bool = False
    proxy: str = ""
    ca_cert: str = ""
    run_token_budget: int = Field(default=0, ge=0, le=10_000_000)
    monthly_token_budget: int = Field(default=0, ge=0, le=100_000_000)
    mode: str = Field(default="auto", pattern="^(auto|semi_trust)$")


ALLOWED_MODELS = {
    "woe_logistic_scorecard",
    "logistic_regression",
    "random_forest",
    "hist_gradient_boosting",
    "xgboost",
}


def public_dataset(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result.pop("path", None)
    return result


def public_dictionary(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result.pop("path", None)
    return result


def public_run(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    state = dict(result.get("state") or {})
    if "safe_evidence" in state:
        state["safe_evidence"] = {"fields": len(state["safe_evidence"].get("fields", [])), "raw_rows_included": False}
    if "code" in state:
        state["code"] = "<generated artifact available locally>"
    result["state"] = state
    return result


def _profile_columns(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in (state.get("profile") or {}).get("columns_detail", [])
        if item.get("name")
    }


def _validate_baseline_column(frame: pd.DataFrame, target: str, column: Optional[str]) -> Optional[str]:
    value = str(column).strip() if column is not None else ""
    if not value:
        return None
    if value not in frame.columns:
        raise HTTPException(400, f"基线分数列不存在：{value}")
    if value == target:
        raise HTTPException(400, "基线分数列不能与 Y 相同")
    numeric = pd.to_numeric(frame[value], errors="coerce")
    if numeric.isna().any() or not numeric.map(lambda item: pd.notna(item) and pd.api.types.is_number(item)).all():
        raise HTTPException(400, "基线分数列必须是完整可解析的数值列")
    return value


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "templates" / "index.html")


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__, "runtime": "local", "provider": ProviderGateway().status(), "tools": len(registry_manifest())}


@app.get("/api/tools")
def list_tools() -> Dict[str, Any]:
    return {"tools": registry_manifest(), "mcp": {"enabled": False, "message": "V1 不允许 Agent 自由发现或调用 MCP 工具。"}}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return {"config": public_config(load_config()), "provider": ProviderGateway().status()}


@app.put("/api/config")
def put_config(payload: ConfigPayload) -> Dict[str, Any]:
    return {"config": save_config(payload.model_dump()), "provider": ProviderGateway().status()}


@app.post("/api/config/test")
def test_provider_config() -> Dict[str, Any]:
    result = ProviderGateway().connectivity_check()
    return {
        "ok": result.ok,
        "error_code": result.error_code,
        "message": result.error_message or "Provider 连通性检查成功。",
        "model": result.model,
    }


@app.get("/api/projects")
def list_projects() -> Dict[str, Any]:
    return {"projects": store.list_projects()}


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> Dict[str, Any]:
    return {"project": store.create_project(payload.name)}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> Dict[str, Any]:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return {
        "project": project,
        "datasets": [public_dataset(item) for item in store.list_datasets(project_id)],
        "dictionaries": [public_dictionary(item) for item in store.list_dictionaries(project_id)],
        "runs": [public_run(item) for item in store.list_runs(project_id)],
    }


def write_demo(project_id: str) -> Path:
    project_dir = store.project_dir(project_id) / "datasets"
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "risk_model_agent_demo.csv"
    if not path.exists():
        import numpy as np
        rng = np.random.default_rng(42)
        rows = 600
        income = rng.normal(8500, 2800, rows).clip(1800, 30000)
        utilization = rng.beta(2, 5, rows)
        inquiries = rng.poisson(2.5, rows)
        channel = rng.choice(["online", "branch", "partner"], rows, p=[0.6, 0.25, 0.15])
        dates = pd.date_range("2023-01-01", periods=rows, freq="D")
        logit = -1.5 - income / 18000 + utilization * 2.3 + inquiries * 0.18 + (channel == "partner") * 0.35
        probability = 1 / (1 + np.exp(-logit))
        target = (rng.random(rows) < probability).astype(int)
        frame = pd.DataFrame({
            "application_date": dates,
            "income": income.round(2),
            "utilization": utilization.round(4),
            "inquiries_30d": inquiries,
            "channel": channel,
            "prior_delinquencies": rng.poisson(0.4, rows),
            "bad_flag": target,
        })
        frame.to_csv(path, index=False)
    return path


@app.post("/api/projects/{project_id}/demo")
def create_demo(project_id: str) -> Dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    path = write_demo(project_id)
    dataset = store.create_dataset(project_id, path.name, path, sha256_file(path), path.stat().st_size, is_demo=True)
    return {"dataset": public_dataset(dataset), "is_demo": True, "message": "已创建本地合成演示数据，不代表真实业务效果。"}


@app.post("/api/projects/{project_id}/datasets")
async def upload_dataset(project_id: str, file: UploadFile = File(...), sheet: Optional[str] = Form(default=None)) -> Dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    filename = Path(file.filename or "upload.csv").name
    if Path(filename).suffix.lower() not in {".csv", ".xlsx"}:
        raise HTTPException(400, "只支持 CSV 和 XLSX")
    destination = store.project_dir(project_id) / "datasets" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Dataset versions are immutable. Re-uploading the same display name must not
    # overwrite the file referenced by an older Run.
    if destination.exists() or destination.is_symlink():
        stem = Path(filename).stem
        suffix = Path(filename).suffix.lower()
        destination = destination.with_name(f"{stem}__{new_id('upload').split('_', 1)[1]}{suffix}")
    size = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件超过本地导入上限")
                handle.write(chunk)
        available_sheets = list_sheets(destination)
        if len(available_sheets) > 1 and not sheet:
            raise HTTPException(400, {"code": "SHEET_REQUIRED", "message": "XLSX 包含多个 Sheet，请明确选择。", "sheets": available_sheets})
        if sheet and available_sheets and sheet not in available_sheets:
            raise HTTPException(400, {"code": "SHEET_NOT_FOUND", "message": "指定 Sheet 不存在。", "sheets": available_sheets})
        frame = read_table(destination, sheet)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"文件预检失败：{exc}")
    dataset = store.create_dataset(project_id, destination.name, destination, sha256_file(destination), size, len(frame), len(frame.columns), sheet, is_demo=False)
    return {"dataset": public_dataset(dataset), "is_demo": False, "message": "文件已留在本机，尚未上传任何外部服务。"}


@app.post("/api/projects/{project_id}/datasets/inspect")
async def inspect_dataset(project_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    filename = Path(file.filename or "inspect.csv").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise HTTPException(400, "只支持 CSV 和 XLSX")
    temporary = store.project_dir(project_id) / "temp" / f"{new_id('inspect')}{suffix}"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件超过本地导入上限")
                handle.write(chunk)
        sheets = list_sheets(temporary)
        if len(sheets) > 1:
            return {"filename": filename, "bytes": size, "sheets": sheets, "requires_sheet": True}
        frame = read_table(temporary)
        profile = {"rows": len(frame), "columns": len(frame.columns), "target_candidates": profile_table(frame).get("target_candidates", [])}
        return {"filename": filename, "bytes": size, "sheets": sheets, "requires_sheet": False, "preflight": profile}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"文件预检失败：{exc}")
    finally:
        temporary.unlink(missing_ok=True)


@app.post("/api/projects/{project_id}/dictionaries")
async def upload_dictionary(project_id: str, file: UploadFile = File(...), sheet: Optional[str] = Form(default=None)) -> Dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    filename = Path(file.filename or "dictionary.csv").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise HTTPException(400, "数据字典只支持 CSV 和 XLSX")
    destination = store.project_dir(project_id) / "dictionaries" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination = destination.with_name(f"{Path(filename).stem}__{new_id('dict').split('_', 1)[1]}{suffix}")
    try:
        size = 0
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "数据字典超过本地导入上限")
                handle.write(chunk)
        sheets = list_sheets(destination)
        if len(sheets) > 1 and not sheet:
            raise HTTPException(400, {"code": "SHEET_REQUIRED", "message": "数据字典包含多个 Sheet，请明确选择。", "sheets": sheets})
        if sheet and sheets and sheet not in sheets:
            raise HTTPException(400, {"code": "SHEET_NOT_FOUND", "message": "指定 Sheet 不存在。", "sheets": sheets})
        frame = read_table(destination, sheet)
        if len(frame.columns) < 1 or len(frame) < 1:
            raise HTTPException(400, "数据字典不能为空")
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"数据字典预检失败：{exc}")
    dictionary = store.create_dictionary(project_id, destination.name, destination, sha256_file(destination), len(frame), len(frame.columns))
    return {"dictionary": public_dictionary(dictionary), "message": "数据字典已版本化保存在本机。"}


@app.get("/api/projects/{project_id}/dictionaries")
def list_dictionaries(project_id: str) -> Dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return {"dictionaries": [public_dictionary(item) for item in store.list_dictionaries(project_id)]}


@app.post("/api/projects/{project_id}/runs")
def create_run(project_id: str, payload: RunCreate) -> Dict[str, Any]:
    project = store.get_project(project_id)
    dataset = store.get_dataset(payload.dataset_id)
    if not project or not dataset or dataset["project_id"] != project_id:
        raise HTTPException(404, "项目或数据集不存在")
    active = [run for run in store.list_runs(project_id) if run["status"] in {"queued", "running", "awaiting_confirmation", "paused"}]
    if active:
        raise HTTPException(409, "该项目已有活动 Run，请先完成、取消或确认已有 Run")
    run = store.create_run(project_id, payload.dataset_id, payload.mode)
    start_run(run)
    return {"run": public_run(run), "message": "Run 已排队，页面将通过事件流显示进度。"}


@app.post("/api/projects/{project_id}/what-if")
def create_what_if(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fork a completed Run into an isolated, explicitly experimental Run."""
    base_run_id = str(payload.get("base_run_id") or payload.get("run_id") or "")
    base = store.get_run(base_run_id)
    if not base or base["project_id"] != project_id:
        raise HTTPException(404, "基线 Run 不存在或不属于当前项目")
    if base["status"] != "succeeded":
        raise HTTPException(409, "what-if 只能从已完成的正式或实验 Run 派生")
    base_state = deepcopy(base.get("state") or {})
    if base_state.get("run_kind") == "experiment" and not payload.get("allow_nested_experiment"):
        raise HTTPException(400, "默认不允许从实验 Run 再次派生，请回到正式 Run")
    changes = payload.get("changes") or {}
    if not isinstance(changes, dict):
        raise HTTPException(400, "what-if changes 必须是对象")
    plan = deepcopy(base_state.get("plan") or {})
    dataset = store.get_dataset(base["dataset_id"])
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    target = str(plan.get("target") or (base_state.get("target") or {}).get("target") or "")
    known_columns = set(_profile_columns(base_state)) or {str(column) for column in frame.columns}
    screening = deepcopy(plan.get("screening") or {})

    if "excluded_features" in changes:
        excluded = changes.get("excluded_features")
        if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded):
            raise HTTPException(400, "what-if excluded_features 必须是字符串列表")
        unknown = [item for item in excluded if item not in known_columns]
        if unknown:
            raise HTTPException(400, f"what-if 排除字段不存在：{unknown[:5]}")
        screening["excluded_columns"] = list(dict.fromkeys(excluded))
    if "min_iv" in changes:
        try:
            min_iv = float(changes["min_iv"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "what-if min_iv 必须是数值") from exc
        if not 0 <= min_iv <= 10:
            raise HTTPException(400, "what-if min_iv 必须在 0—10 之间")
        screening["min_iv"] = min_iv
    if "max_features" in changes:
        try:
            max_features = int(changes["max_features"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "what-if max_features 必须是整数") from exc
        if not 1 <= max_features <= 5000:
            raise HTTPException(400, "what-if max_features 必须在 1—5000 之间")
        screening["max_features"] = max_features
    if "models" in changes:
        models = changes.get("models")
        if not isinstance(models, list):
            raise HTTPException(400, "what-if models 必须是列表")
        models = list(dict.fromkeys(str(item) for item in models))
        if not models or any(item not in ALLOWED_MODELS for item in models):
            raise HTTPException(400, "what-if models 为空或包含不支持的模型")
        plan["models"] = models
    if "split_method" in changes:
        split_method = changes.get("split_method")
        if split_method not in {"time_holdout", "stratified_holdout"}:
            raise HTTPException(400, "what-if 不支持的样本切分方式")
        if split_method == "time_holdout" and not plan.get("time_column_suggestion"):
            raise HTTPException(400, "当前数据没有可用时间字段，不能选择时间切分")
        plan["split"] = {**(plan.get("split") or {}), "method": split_method}
    if "baseline_column" in changes:
        plan["baseline_column"] = _validate_baseline_column(frame, target, changes.get("baseline_column"))
    plan["screening"] = screening

    for key in ("selection", "split", "training", "code", "code_review", "report"):
        base_state.pop(key, None)
    # A fork gets a new durable Run identity; never let the parent run_id leak
    # into the child worker state.
    base_state.pop("run_id", None)
    base_state.update(
        {
            "project_id": project_id,
            "dataset_id": base["dataset_id"],
            "plan": plan,
            "confirmed": True,
            "mode": "auto",
            "run_kind": "experiment",
            "parent_run_id": base_run_id,
            "experiment_changes": changes,
        }
    )
    run = store.create_run(project_id, base["dataset_id"], "auto", initial_state=base_state, phase="screening")
    store.add_decision(run["id"], "what_if_fork", {"base_run_id": base_run_id, "changes": changes})
    start_run(run, initial_state=base_state, start="screen")
    return {"run": public_run(run), "message": "what-if 实验已隔离创建；不会覆盖正式 Run 报告。"}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    return {"run": public_run(run), "events": store.list_events(run_id)[-20:]}


@app.post("/api/runs/{run_id}/decision")
def confirm_decision(run_id: str, payload: DecisionPayload) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    if run["status"] != "awaiting_confirmation":
        raise HTTPException(409, "当前 Run 不在等待确认状态")
    state = dict(run.get("state") or {})
    plan = dict(state.get("plan") or {})
    values = dict(payload.values or {})
    target = values.get("target") or plan.get("target")
    candidates = set((state.get("profile") or {}).get("target_candidates") or [])
    if target not in candidates:
        raise HTTPException(400, "确认的 Y 必须来自通过 0/1 契约检查的候选字段")
    if target != (state.get("target") or {}).get("target"):
        dataset = store.get_dataset(run["dataset_id"])
        if not dataset:
            raise HTTPException(404, "数据集不存在")
        candidate_target = target_summary(read_table(Path(dataset["path"]), dataset.get("sheet")), target)
        if not candidate_target.get("contract_ok"):
            raise HTTPException(400, "确认的 Y 未通过 0/1 契约检查")
        state["target"] = candidate_target
    requested_models = values.get("models")
    if requested_models is not None:
        if not isinstance(requested_models, list):
            raise HTTPException(400, "候选模型必须是列表")
        requested_models = list(dict.fromkeys(str(item) for item in requested_models))
        if not requested_models or any(item not in ALLOWED_MODELS for item in requested_models):
            raise HTTPException(400, "候选模型列表为空或包含不支持的模型")
        plan["models"] = requested_models
    split_method = values.get("split_method")
    if split_method:
        if split_method not in {"time_holdout", "stratified_holdout"}:
            raise HTTPException(400, "不支持的样本切分方式")
        if split_method == "time_holdout" and not plan.get("time_column_suggestion"):
            raise HTTPException(400, "当前数据没有可用时间字段，不能选择时间切分")
        plan["split"] = {**(plan.get("split") or {}), "method": split_method}
    excluded = values.get("excluded_features")
    if excluded is not None:
        if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded):
            raise HTTPException(400, "手动排除字段必须是字符串列表")
        known_columns = {item.get("name") for item in (state.get("profile") or {}).get("columns_detail", [])}
        unknown = [item for item in excluded if item not in known_columns]
        if unknown:
            raise HTTPException(400, f"手动排除字段不存在：{unknown[:5]}")
        plan["screening"] = {**(plan.get("screening") or {}), "excluded_columns": list(dict.fromkeys(excluded))}
    dataset = store.get_dataset(run["dataset_id"])
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    baseline_column = values.get("baseline_column")
    baseline_column = _validate_baseline_column(read_table(Path(dataset["path"]), dataset.get("sheet")), target, baseline_column)
    plan["baseline_column"] = baseline_column
    plan["target"] = target
    state["plan"] = plan
    state["confirmed"] = True
    store.add_decision(run_id, payload.kind, {**values, "target": target, "models": plan.get("models"), "split_method": plan.get("split", {}).get("method"), "excluded_features": plan.get("screening", {}).get("excluded_columns", []), "baseline_column": baseline_column})
    store.update_run(run_id, status="queued", phase="screening", state=state)
    store.append_event(run_id, "decision_confirmed", {"node": "cleaning", "status": "queued", "kind": payload.kind, "message": "用户已确认关键建模决定，继续本地筛选与训练。"})
    resume_after_confirmation(store.get_run(run_id))
    return {"run": public_run(store.get_run(run_id))}


@app.post("/api/runs/{run_id}/clean")
def apply_run_cleaning(run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    if run["status"] != "awaiting_confirmation" or run["phase"] != "cleaning":
        raise HTTPException(409, "只有清洗确认节点可以执行批准的清洗动作")
    state = dict(run.get("state") or {})
    cleaning = dict(state.get("cleaning") or {})
    if cleaning.get("execution"):
        raise HTTPException(409, "当前 Run 已执行过清洗；请基于新数据版本重新开始")
    actions = payload.get("actions") or []
    if not isinstance(actions, list):
        raise HTTPException(400, "清洗 actions 必须是列表")
    dataset = store.get_dataset(run["dataset_id"])
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    try:
        require_tool("apply_cleaning_plan", "cleaning")
        frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
        cleaned, execution = apply_cleaning_plan(frame, cleaning, actions)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    destination = store.project_dir(run["project_id"]) / "datasets" / f"{Path(dataset['filename']).stem}__cleaned_{new_id('version').split('_', 1)[1]}.csv"
    cleaned.to_csv(destination, index=False)
    new_dataset = store.create_dataset(run["project_id"], destination.name, destination, sha256_file(destination), destination.stat().st_size, len(cleaned), len(cleaned.columns), None, is_demo=False)
    target_name = (state.get("plan") or {}).get("target") or (state.get("target") or {}).get("target")
    new_profile = profile_table(cleaned)
    new_quality = quality_analysis(cleaned, target=target_name)
    state["profile"] = new_profile
    state["quality"] = new_quality
    if target_name and target_name in cleaned.columns:
        state["target"] = target_summary(cleaned, target_name)
    store.update_dataset_profile(new_dataset["id"], new_profile)
    cleaning["execution"] = execution
    state["cleaning"] = cleaning
    state["dataset_version_parent"] = dataset["id"]
    state["dataset_id"] = new_dataset["id"]
    store.add_decision(run_id, "cleaning_execution", {"actions": actions, "execution": execution, "new_dataset_id": new_dataset["id"]})
    store.update_run_dataset(run_id, new_dataset["id"])
    store.update_run(run_id, status="awaiting_confirmation", phase="cleaning", state=state)
    store.append_event(run_id, "cleaning_applied", {"node": "cleaning", "status": "awaiting_confirmation", "message": "已按批准动作生成新的本地数据版本；原始版本保留。", "dataset_id": new_dataset["id"], "execution": execution})
    return {"run": public_run(store.get_run(run_id)), "dataset": public_dataset(new_dataset), "execution": execution}


@app.post("/api/runs/{run_id}/pause")
def pause_run(run_id: str) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    if run["status"] not in {"queued", "running"}:
        raise HTTPException(409, "当前 Run 只能从排队或运行中暂停")
    store.update_run(run_id, status="paused", phase=run["phase"], state=run.get("state") or {})
    store.append_event(run_id, "run_paused", {"node": run["phase"], "status": "paused", "message": "Run 已暂停；当前已持久化节点边界可在本地恢复。"})
    return {"run": public_run(store.get_run(run_id))}


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    if run["status"] != "paused":
        raise HTTPException(409, "当前 Run 不在暂停状态")
    store.update_run(run_id, status="queued", phase=run["phase"], state=run.get("state") or {})
    store.append_event(run_id, "run_resumed", {"node": run["phase"], "status": "queued", "message": "Run 已恢复，将从最近安全节点继续。"})
    resume_after_pause(store.get_run(run_id))
    return {"run": public_run(store.get_run(run_id))}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> Dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    if run["status"] in {"succeeded", "failed", "blocked", "cancelled"}:
        raise HTTPException(409, "当前 Run 已经结束")
    store.update_run(run_id, status="cancelled", phase=run["phase"], state=run.get("state") or {})
    store.append_event(run_id, "run_cancelled", {"node": run["phase"], "status": "cancelled", "message": "Run 已取消，未完成产物不会被当作正式结果。"})
    return {"run": public_run(store.get_run(run_id))}


@app.post("/api/runs/{run_id}/feedback")
def add_feedback(run_id: str, payload: FeedbackPayload) -> Dict[str, Any]:
    if not store.get_run(run_id):
        raise HTTPException(404, "Run 不存在")
    store.add_feedback(run_id, payload.reaction, payload.reason, payload.event_id)
    return {"ok": True, "message": "反馈已记录，不会直接改变正式确认状态。"}


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str, after: int = 0) -> Dict[str, Any]:
    if not store.get_run(run_id):
        raise HTTPException(404, "Run 不存在")
    return {"events": store.list_events(run_id, after)}


@app.get("/api/runs/{run_id}/events/stream")
async def stream_events(run_id: str, after: int = 0) -> StreamingResponse:
    if not store.get_run(run_id):
        raise HTTPException(404, "Run 不存在")

    async def generator():
        cursor = after
        idle = 0
        while idle < 300:
            events = store.list_events(run_id, cursor)
            if events:
                idle = 0
                for event in events:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                yield ": heartbeat\n\n"
            run = store.get_run(run_id)
            if run and run["status"] in {"succeeded", "failed", "blocked", "cancelled", "paused"} and not events:
                break
            await asyncio.sleep(1)

    return StreamingResponse(generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/report")
def get_report(run_id: str) -> JSONResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    report_path = store.run_dir(run["project_id"], run_id) / "report.json"
    if not report_path.exists():
        raise HTTPException(404, "报告尚未生成")
    return JSONResponse(json.loads(report_path.read_text(encoding="utf-8")))


@app.get("/api/runs/{run_id}/report.html")
def report_html(run_id: str) -> FileResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    path = store.run_dir(run["project_id"], run_id) / "report.html"
    if not path.exists():
        raise HTTPException(404, "报告尚未生成")
    return FileResponse(path, media_type="text/html")


@app.get("/api/runs/{run_id}/report.xlsx")
def report_xlsx(run_id: str) -> FileResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    path = store.run_dir(run["project_id"], run_id) / "report.xlsx"
    if not path.exists():
        raise HTTPException(404, "XLSX 报告尚未生成")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="risk-model-report.xlsx")


@app.get("/api/runs/{run_id}/artifacts.zip")
def artifacts_zip(run_id: str) -> FileResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run 不存在")
    run_dir = store.run_dir(run["project_id"], run_id)
    files = [path for path in run_dir.rglob("*") if path.is_file() and path.name != "artifacts.zip"]
    if not files:
        raise HTTPException(404, "交付物尚未生成")
    archive = run_dir / "artifacts.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in files:
            handle.write(path, path.relative_to(run_dir))
    return FileResponse(archive, media_type="application/zip", filename="risk-model-artifacts.zip")


@app.get("/api/projects/{project_id}/backup.zip")
def project_backup(project_id: str, include_data: bool = False) -> FileResponse:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    project_dir = store.project_dir(project_id)
    export_dir = project_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    archive = export_dir / f"{new_id('backup')}.zip"
    snapshot = store.project_snapshot(project_id)
    snapshot["raw_data_included"] = bool(include_data)
    files = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file() or export_dir in path.parents:
            continue
        relative = path.relative_to(project_dir)
        if not include_data and relative.parts and relative.parts[0] == "datasets":
            continue
        files.append({"path": str(relative), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    snapshot["files"] = files
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("manifest.json", json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2))
        for item in files:
            handle.write(project_dir / item["path"], item["path"])
    return FileResponse(archive, media_type="application/zip", filename=f"{project_id}-backup.zip")


@app.post("/api/projects/{project_id}/analysis")
def run_analysis(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    dataset = store.get_dataset(str(payload.get("dataset_id")))
    if not dataset or dataset["project_id"] != project_id:
        raise HTTPException(404, "数据集不存在")
    require_tool("segment_analysis", "analysis")
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    result = segment_analysis(frame, payload.get("spec") or {})
    return {"analysis": result, "raw_data_uploaded": False}


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=os.getenv("RISK_AGENT_HOST", "127.0.0.1"), port=int(os.getenv("RISK_AGENT_PORT", "8765")), reload=False)


if __name__ == "__main__":
    run()
