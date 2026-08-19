from __future__ import annotations

import asyncio
import json
import os
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
from .worker import read_table, segment_analysis

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
    api_key: str = ""
    clear_api_key: bool = False
    proxy: str = ""
    ca_cert: str = ""
    run_token_budget: int = Field(default=0, ge=0, le=10_000_000)
    monthly_token_budget: int = Field(default=0, ge=0, le=100_000_000)
    mode: str = Field(default="auto", pattern="^(auto|semi_trust)$")


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
        frame = read_table(destination, sheet)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"文件预检失败：{exc}")
    dataset = store.create_dataset(project_id, destination.name, destination, sha256_file(destination), size, len(frame), len(frame.columns), sheet, is_demo=False)
    return {"dataset": public_dataset(dataset), "is_demo": False, "message": "文件已留在本机，尚未上传任何外部服务。"}


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
    store.add_decision(run_id, payload.kind, payload.values)
    state = dict(run.get("state") or {})
    state["confirmed"] = True
    store.update_run(run_id, status="queued", phase="screening", state=state)
    store.append_event(run_id, "decision_confirmed", {"node": "planning", "status": "queued", "kind": payload.kind, "message": "用户已确认关键建模决定，继续本地筛选与训练。"})
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
