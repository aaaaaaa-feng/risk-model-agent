from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from app.core.database import new_id, now_iso
from app.core.config import SettingsStore
from app.runtime import AppContext
from app.workers.io import read_table
from app.workers.profiling import diagnose_frame, profile_frame, target_summary

from .dependencies import context


router = APIRouter(tags=["notebooks"])


class NotebookCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=160)
    dataset_version_id: str | None = None
    template: str = "blank"
    base_asset_id: str | None = None
    right_asset_id: str | None = None
    left_keys: list[str] = Field(default_factory=list)
    right_keys: list[str] = Field(default_factory=list)


class NotebookSave(BaseModel):
    notebook: dict[str, Any]


class CellExecute(BaseModel):
    cell_index: int = Field(ge=0)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


class NotebookImportOutput(BaseModel):
    relative_path: str
    label: str = Field(min_length=1, max_length=240)
    parent_dataset_version_id: str | None = None
    target_column: str | None = None
    customer_key: str | None = None
    expected_grain: str = "same_or_fewer_rows"
    sheet: str | None = None


@router.post("/notebooks", status_code=201)
def create_notebook(payload: NotebookCreate, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.get_project(payload.project_id)
    if payload.dataset_version_id:
        dataset = ctx.catalog.require("dataset_versions", payload.dataset_version_id)
        if dataset["project_id"] != payload.project_id:
            raise ValueError("CROSS_PROJECT_NOTEBOOK_FORBIDDEN")
    identifier = new_id("nb")
    path = ctx.notebooks.create(payload.project_id, identifier, payload.name)
    if payload.template == "agent_join":
        if not payload.base_asset_id or not payload.right_asset_id:
            raise ValueError("JOIN_NOTEBOOK_ASSETS_REQUIRED")
        base = ctx.catalog.require("data_assets", payload.base_asset_id)
        right = ctx.catalog.require("data_assets", payload.right_asset_id)
        if base["project_id"] != payload.project_id or right["project_id"] != payload.project_id:
            raise ValueError("CROSS_PROJECT_NOTEBOOK_FORBIDDEN")
        document = ctx.notebooks.read(path)
        import nbformat

        code = f'''from pathlib import Path
import pandas as pd

# Agent 生成的本地关联草稿；请逐单元格核对后再生成数据版本。
left = pd.read_csv(r"{base['stored_path']}") if r"{base['stored_path']}".lower().endswith(".csv") else pd.read_excel(r"{base['stored_path']}", sheet_name={base.get('sheet')!r})
right = pd.read_csv(r"{right['stored_path']}") if r"{right['stored_path']}".lower().endswith(".csv") else pd.read_excel(r"{right['stored_path']}", sheet_name={right.get('sheet')!r})
left_keys = {payload.left_keys!r}
right_keys = {payload.right_keys!r}
assert left_keys and len(left_keys) == len(right_keys), "请先填写关联键"
assert not right.duplicated(right_keys).any(), "右表关联键不唯一，需先聚合或去重"
joined = left.merge(right, how="left", left_on=left_keys, right_on=right_keys, suffixes=("", "_right"), validate="many_to_one")
assert len(joined) == len(left), "关联发生样本膨胀"
joined.to_csv("joined_output.csv", index=False, encoding="utf-8-sig")
{{"left_rows": len(left), "output_rows": len(joined), "output_columns": len(joined.columns)}}'''
        document["cells"].append(nbformat.v4.new_code_cell(code))
        ctx.notebooks.save(path, document)
    timestamp = now_iso()
    record = ctx.database.insert(
        "notebooks",
        {
            "id": identifier,
            "project_id": payload.project_id,
            "dataset_version_id": payload.dataset_version_id,
            "name": payload.name,
            "path": str(path),
            "kernel_id": payload.project_id,
            "status": "idle",
            "metadata_json": {"network_default": "enabled", "security_boundary": "not_a_sandbox"},
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    return {"notebook": record, "document": ctx.notebooks.read(path)}


@router.post("/notebooks/import", status_code=201)
async def import_notebook(
    project_id: str = Form(...),
    file: UploadFile = File(...),
    dataset_version_id: str | None = Form(None),
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    ctx.catalog.get_project(project_id)
    if not (file.filename or "").lower().endswith(".ipynb"):
        raise ValueError("NOTEBOOK_FORMAT_REQUIRED")
    identifier = new_id("nb")
    path = ctx.notebooks.notebook_dir(project_id) / f"{identifier}.ipynb"
    try:
        with path.open("wb") as target:
            while chunk := await file.read(2 * 1024 * 1024):
                target.write(chunk)
        document = ctx.notebooks.read(path)
        ctx.notebooks.save(path, document)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    timestamp = now_iso()
    record = ctx.database.insert(
        "notebooks",
        {
            "id": identifier,
            "project_id": project_id,
            "dataset_version_id": dataset_version_id,
            "name": Path(file.filename or "Imported Notebook").stem[:160],
            "path": str(path),
            "kernel_id": project_id,
            "status": "idle",
            "metadata_json": {"imported": True, "network_default": "enabled", "security_boundary": "not_a_sandbox"},
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    return {"notebook": record, "document": document}


@router.get("/projects/{project_id}/notebooks")
def list_notebooks(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.get_project(project_id)
    return {"notebooks": ctx.database.list("notebooks", {"project_id": project_id}, limit=5000)}


@router.get("/notebooks/{notebook_id}")
def get_notebook(notebook_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    return {"notebook": record, "document": ctx.notebooks.read(Path(record["path"]))}


@router.put("/notebooks/{notebook_id}")
def save_notebook(notebook_id: str, payload: NotebookSave, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    ctx.notebooks.save(Path(record["path"]), payload.notebook)
    record = ctx.database.update("notebooks", notebook_id, {"updated_at": now_iso()})
    return {"notebook": record}


@router.post("/notebooks/{notebook_id}/execute-cell")
def execute_cell(notebook_id: str, payload: CellExecute, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    ctx.database.update("notebooks", notebook_id, {"status": "running", "updated_at": now_iso()})
    try:
        result = ctx.notebooks.execute_cell(
            record["project_id"], Path(record["path"]), payload.cell_index, payload.timeout_seconds
        )
        status = "idle" if result["status"] == "succeeded" else "error"
        ctx.database.update("notebooks", notebook_id, {"status": status, "updated_at": now_iso()})
        return {"execution": result, "network_status": _network_status(ctx), "security_boundary": "user_code_not_sandboxed"}
    except Exception:
        ctx.database.update("notebooks", notebook_id, {"status": "error", "updated_at": now_iso()})
        raise


@router.post("/notebooks/{notebook_id}/execute-all")
def execute_all(notebook_id: str, timeout_seconds: int = 300, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    results = ctx.notebooks.execute_all(record["project_id"], Path(record["path"]), timeout_seconds)
    status = "error" if any(item["status"] == "failed" for item in results) else "idle"
    ctx.database.update("notebooks", notebook_id, {"status": status, "updated_at": now_iso()})
    return {"executions": results, "network_status": _network_status(ctx), "security_boundary": "user_code_not_sandboxed"}


@router.post("/notebooks/{notebook_id}/dataset-versions", status_code=201)
def import_notebook_output(
    notebook_id: str,
    payload: NotebookImportOutput,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    root = Path(record["path"]).parent.resolve()
    output = (root / payload.relative_path).resolve()
    if output != root and root not in output.parents:
        raise ValueError("NOTEBOOK_OUTPUT_OUTSIDE_PROJECT")
    if not output.exists():
        raise ValueError("NOTEBOOK_OUTPUT_NOT_FOUND")
    frame = read_table(output, payload.sheet)
    validation: dict[str, Any] = {
        "rows": len(frame),
        "columns": len(frame.columns),
        "duplicate_rows": int(frame.duplicated().sum()),
        "grain": "unknown",
        "inflation_ratio": None,
    }
    parents: list[str] = [notebook_id]
    if payload.parent_dataset_version_id:
        parent = ctx.catalog.require("dataset_versions", payload.parent_dataset_version_id)
        if parent["project_id"] != record["project_id"]:
            raise ValueError("CROSS_PROJECT_NOTEBOOK_OUTPUT_FORBIDDEN")
        parents.append(parent["id"])
        validation["inflation_ratio"] = len(frame) / max(int(parent["rows"]), 1)
        if payload.expected_grain == "same_or_fewer_rows" and validation["inflation_ratio"] > 1.001:
            raise ValueError("NOTEBOOK_OUTPUT_SAMPLE_INFLATION")
    if payload.customer_key:
        if payload.customer_key not in frame:
            raise ValueError("CUSTOMER_KEY_NOT_FOUND")
        validation["grain"] = "customer" if frame[payload.customer_key].is_unique else "order_or_event"
    target_columns = [payload.target_column] if payload.target_column else profile_frame(frame)["binary_candidates"]
    validation["target_checks"] = {}
    for target_column in target_columns:
        diagnostics = diagnose_frame(frame, target_column)
        validation["target_checks"][target_column] = diagnostics["target"]
        blocking = [item for item in diagnostics["issues"] if item.get("severity") == "blocking"]
        if blocking:
            raise ValueError(blocking[0]["code"])
    if payload.parent_dataset_version_id:
        parent_frame = ctx.catalog.dataset_frame(payload.parent_dataset_version_id)
        for target_column in target_columns:
            if target_column not in parent_frame:
                continue
            before = target_summary(parent_frame, target_column)
            after = target_summary(frame, target_column)
            before_counts = (before["positive_count"], before["negative_count"], before["invalid_count"], before["missing_count"])
            after_counts = (after["positive_count"], after["negative_count"], after["invalid_count"], after["missing_count"])
            if before_counts != after_counts:
                raise ValueError("NOTEBOOK_OUTPUT_TARGET_DISTRIBUTION_CHANGED")
    version = ctx.catalog.create_dataset_version(
        record["project_id"],
        frame,
        payload.label,
        parents,
        {"kind": "notebook_output", "notebook_id": notebook_id, "validation": validation},
    )
    return {"dataset_version": version, "validation": validation}


@router.delete("/notebooks/{notebook_id}/kernel")
def shutdown_kernel(notebook_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    ctx.notebooks.shutdown_project(record["project_id"])
    ctx.database.update("notebooks", notebook_id, {"status": "idle", "updated_at": now_iso()})
    return {"stopped": True}


def _network_status(ctx: AppContext) -> str:
    enabled = SettingsStore(ctx.paths).load().notebook_network
    return "enabled" if enabled else "disabled_preference_not_os_sandboxed"
