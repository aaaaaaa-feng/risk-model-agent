from __future__ import annotations

import asyncio
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
from .orchestrator import resume_after_confirmation, start_run
from .storage import sha256_file, store
from .worker import list_sheets, profile_table, read_table, segment_analysis, target_summary

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
    "logistic_regression",
    "random_forest",
    "hist_gradient_boosting",
    "xgboost",
}


def public_dataset(item: Dict[str, Any]) -> Dict[str, Any]:
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


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "templates" / "index.html")


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": __version__, "runtime": "local", "provider": ProviderGateway().status()}


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


@app.post("/api/projects/{project_id}/runs")
def create_run(project_id: str, payload: RunCreate) -> Dict[str, Any]:
    project = store.get_project(project_id)
    dataset = store.get_dataset(payload.dataset_id)
    if not project or not dataset or dataset["project_id"] != project_id:
        raise HTTPException(404, "项目或数据集不存在")
    active = [run for run in store.list_runs(project_id) if run["status"] in {"queued", "running", "awaiting_confirmation"}]
    if active:
        raise HTTPException(409, "该项目已有活动 Run，请先完成、取消或确认已有 Run")
    run = store.create_run(project_id, payload.dataset_id, payload.mode)
    start_run(run)
    return {"run": public_run(run), "message": "Run 已排队，页面将通过事件流显示进度。"}


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
    plan["target"] = target
    state["plan"] = plan
    state["confirmed"] = True
    store.add_decision(run_id, payload.kind, {**values, "target": target, "models": plan.get("models"), "split_method": plan.get("split", {}).get("method"), "excluded_features": plan.get("screening", {}).get("excluded_columns", [])})
    store.update_run(run_id, status="queued", phase="screening", state=state)
    store.append_event(run_id, "decision_confirmed", {"node": "cleaning", "status": "queued", "kind": payload.kind, "message": "用户已确认关键建模决定，继续本地筛选与训练。"})
    resume_after_confirmation(store.get_run(run_id))
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
            if run and run["status"] in {"succeeded", "failed", "blocked", "cancelled"} and not events:
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


@app.post("/api/projects/{project_id}/analysis")
def run_analysis(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    dataset = store.get_dataset(str(payload.get("dataset_id")))
    if not dataset or dataset["project_id"] != project_id:
        raise HTTPException(404, "数据集不存在")
    frame = read_table(Path(dataset["path"]), dataset.get("sheet"))
    result = segment_analysis(frame, payload.get("spec") or {})
    return {"analysis": result, "raw_data_uploaded": False}


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=os.getenv("RISK_AGENT_HOST", "127.0.0.1"), port=int(os.getenv("RISK_AGENT_PORT", "8765")), reload=False)


if __name__ == "__main__":
    run()
