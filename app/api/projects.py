from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.config import MAX_UPLOAD_BYTES
from app.core.database import new_id
from app.core.errors import normalize_error_code, public_error_message
from app.bootstrap import AppContext
from app.workers.io import SUPPORTED_SUFFIXES, safe_file_name
from app.workers.io import read_table
from app.workers.profiling import infer_type
from app.workers.demo import install_demo_project

from .dependencies import context


router = APIRouter(tags=["projects-and-data"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    mode: str = "semi_trusted"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    mode: str | None = None
    metadata: dict[str, Any] | None = None


class SheetChoice(BaseModel):
    sheet: str


class DictionaryAttach(BaseModel):
    dictionary_asset_id: str


class JoinPreview(BaseModel):
    left_asset_id: str
    right_asset_id: str
    left_keys: list[str]
    right_keys: list[str]
    target_columns: list[str] = Field(default_factory=list)
    customer_key: str | None = None


class JoinPlanCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=160)
    base_asset_id: str
    steps: list[dict[str, Any]]


class JoinExecute(BaseModel):
    target_columns: list[str] = Field(default_factory=list)
    customer_key: str | None = None


class TargetTasksCreate(BaseModel):
    project_id: str
    dataset_version_id: str
    target_columns: list[str] = Field(min_length=1)
    labels: dict[str, Any] | None = None


class DemoProjectCreate(BaseModel):
    name: str = Field(default="多表风控建模演示", min_length=1, max_length=120)
    mode: str = "semi_trusted"
    rows: int = Field(default=1_200, ge=500, le=30_000)
    seed: int = 20260821


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"project": ctx.catalog.create_project(**payload.model_dump())}


@router.post("/projects/demo", status_code=201)
def create_demo_project(
    payload: DemoProjectCreate,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    return install_demo_project(ctx.catalog, **payload.model_dump())


@router.get("/projects")
def list_projects(
    include_archived: bool = True, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {"projects": ctx.catalog.list_projects(include_archived)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {
        "project": ctx.catalog.get_project(project_id),
        "assets": ctx.database.list("data_assets", {"project_id": project_id}, limit=2000),
        "dataset_versions": ctx.database.list(
            "dataset_versions", {"project_id": project_id}, limit=2000
        ),
        "target_tasks": ctx.database.list(
            "target_tasks", {"project_id": project_id}, order_by="queue_position ASC", limit=2000
        ),
        "runs": [
            _run_summary(item)
            for item in ctx.database.list("runs", {"project_id": project_id}, limit=2000)
        ],
    }


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str, payload: ProjectUpdate, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    if "metadata" in data:
        data["metadata_json"] = data.pop("metadata")
    return {"project": ctx.catalog.update_project(project_id, data)}


@router.post("/projects/{project_id}/archive")
def archive_project(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"project": ctx.catalog.archive_project(project_id)}


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"project": ctx.catalog.restore_project(project_id)}


@router.delete("/projects/{project_id}")
def trash_project(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"project": ctx.catalog.trash_project(project_id), "recoverable": True}


@router.post("/projects/{project_id}/data-assets", status_code=201)
async def upload_asset(
    project_id: str,
    file: UploadFile = File(...),
    kind: str = Form("feature"),
    sheet: str | None = Form(None),
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    name = file.filename or "upload.csv"
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(400, detail={"code": "UPLOAD_FORMAT_UNSUPPORTED"})
    incoming = ctx.paths.project_dir(project_id) / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    staged = incoming / f"{new_id('upload')}-{safe_file_name(name)}"
    size = 0
    try:
        with staged.open("wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, detail={"code": "UPLOAD_TOO_LARGE"})
                target.write(chunk)
        try:
            asset = ctx.catalog.register_asset(project_id, staged, name, kind, sheet)
        except KeyError:
            staged.unlink(missing_ok=True)
            raise
        except Exception as exc:
            staged.unlink(missing_ok=True)
            code = normalize_error_code(exc, "DATA_ASSET_READ_FAILED")
            raise ValueError(code) from exc
        return {"asset": asset}
    finally:
        staged.unlink(missing_ok=True)
        await file.close()


@router.get("/projects/{project_id}/data-assets")
def list_assets(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.get_project(project_id)
    return {"assets": ctx.database.list("data_assets", {"project_id": project_id}, limit=5000)}


@router.get("/data-assets/{asset_id}")
def get_asset(asset_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"asset": ctx.catalog.require("data_assets", asset_id)}


@router.get("/data-assets/{asset_id}/schema")
def get_asset_schema(asset_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    asset = ctx.catalog.require("data_assets", asset_id)
    frame = read_table(Path(asset["stored_path"]), asset.get("sheet"), nrows=200)
    return {
        "asset_id": asset_id,
        "columns": [
            {"name": str(column), "type": infer_type(frame[column])} for column in frame.columns
        ],
        "sample_values_included": False,
    }


@router.put("/data-assets/{asset_id}/sheet")
def choose_sheet(
    asset_id: str, payload: SheetChoice, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {"asset": ctx.catalog.choose_asset_sheet(asset_id, payload.sheet)}


@router.post("/data-assets/{asset_id}/dictionary")
def attach_dictionary(
    asset_id: str, payload: DictionaryAttach, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {"asset": ctx.catalog.attach_dictionary(asset_id, payload.dictionary_asset_id)}


@router.post("/data-assets/{asset_id}/materialize", status_code=201)
def materialize(asset_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"dataset_version": ctx.catalog.materialize_asset(asset_id)}


@router.get("/projects/{project_id}/dataset-versions")
def list_dataset_versions(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.get_project(project_id)
    return {
        "dataset_versions": ctx.database.list(
            "dataset_versions", {"project_id": project_id}, limit=5000
        )
    }


@router.get("/dataset-versions/{dataset_version_id}")
def get_dataset_version(
    dataset_version_id: str, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return {"dataset_version": ctx.catalog.require("dataset_versions", dataset_version_id)}


@router.get("/join-plans/recommend")
def recommend_join(
    left_asset_id: str, right_asset_id: str, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return ctx.catalog.recommend_join(left_asset_id, right_asset_id)


@router.post("/join-plans/preview")
def preview_join(payload: JoinPreview, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"validation": ctx.catalog.preview_join(**payload.model_dump())}


@router.post("/join-plans", status_code=201)
def create_join_plan(payload: JoinPlanCreate, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"join_plan": ctx.catalog.create_join_plan(**payload.model_dump())}


@router.get("/projects/{project_id}/join-plans")
def list_join_plans(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"join_plans": ctx.database.list("join_plans", {"project_id": project_id}, limit=5000)}


@router.post("/join-plans/{plan_id}/execute")
def execute_join_plan(
    plan_id: str, payload: JoinExecute, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    plan, dataset = ctx.catalog.execute_join_plan(plan_id, **payload.model_dump())
    return {"join_plan": plan, "dataset_version": dataset}


@router.post("/target-tasks", status_code=201)
def create_target_tasks(
    payload: TargetTasksCreate, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    tasks = [
        ctx.catalog.create_target_task(
            payload.project_id, payload.dataset_version_id, target, payload.labels
        )
        for target in payload.target_columns
    ]
    return {"target_tasks": tasks}


@router.get("/projects/{project_id}/target-tasks")
def list_target_tasks(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {
        "target_tasks": ctx.database.list(
            "target_tasks", {"project_id": project_id}, order_by="queue_position ASC", limit=5000
        )
    }


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    value = {key: item for key, item in run.items() if key != "state"}
    if value.get("error"):
        code = normalize_error_code(value["error"], "RUN_EXECUTION_FAILED")
        value["error"] = code
        value["error_message"] = public_error_message(code)
    return value
