from __future__ import annotations

import hashlib
import json
import numbers
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from app.core.database import new_id, now_iso
from app.core.config import SettingsStore
from app.runtime import AppContext
from app.workers.io import read_table
from app.workers.profiling import diagnose_frame, normalize_binary, profile_frame

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
    key_columns: list[str] = Field(default_factory=list)
    expected_grain: Literal["same_or_fewer_rows", "same_rows"] = "same_or_fewer_rows"
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
left = pd.read_csv(r"{base["stored_path"]}") if r"{base["stored_path"]}".lower().endswith(".csv") else pd.read_excel(r"{base["stored_path"]}", sheet_name={base.get("sheet")!r})
right = pd.read_csv(r"{right["stored_path"]}") if r"{right["stored_path"]}".lower().endswith(".csv") else pd.read_excel(r"{right["stored_path"]}", sheet_name={right.get("sheet")!r})
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
    if dataset_version_id:
        dataset = ctx.catalog.require("dataset_versions", dataset_version_id)
        if dataset["project_id"] != project_id:
            raise ValueError("CROSS_PROJECT_NOTEBOOK_FORBIDDEN")
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
            "metadata_json": {
                "imported": True,
                "network_default": "enabled",
                "security_boundary": "not_a_sandbox",
            },
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
def save_notebook(
    notebook_id: str, payload: NotebookSave, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    ctx.notebooks.save(Path(record["path"]), payload.notebook)
    record = ctx.database.update("notebooks", notebook_id, {"updated_at": now_iso()})
    return {"notebook": record}


@router.post("/notebooks/{notebook_id}/execute-cell")
def execute_cell(
    notebook_id: str, payload: CellExecute, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    ctx.database.update("notebooks", notebook_id, {"status": "running", "updated_at": now_iso()})
    try:
        result = ctx.notebooks.execute_cell(
            record["project_id"], Path(record["path"]), payload.cell_index, payload.timeout_seconds
        )
        status = "idle" if result["status"] == "succeeded" else "error"
        ctx.database.update("notebooks", notebook_id, {"status": status, "updated_at": now_iso()})
        return {
            "execution": result,
            "network_status": _network_status(ctx),
            "security_boundary": "user_code_not_sandboxed",
        }
    except Exception:
        ctx.database.update("notebooks", notebook_id, {"status": "error", "updated_at": now_iso()})
        raise


@router.post("/notebooks/{notebook_id}/execute-all")
def execute_all(
    notebook_id: str, timeout_seconds: int = 300, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    record = ctx.catalog.require("notebooks", notebook_id)
    results = ctx.notebooks.execute_all(record["project_id"], Path(record["path"]), timeout_seconds)
    status = "error" if any(item["status"] == "failed" for item in results) else "idle"
    ctx.database.update("notebooks", notebook_id, {"status": status, "updated_at": now_iso()})
    return {
        "executions": results,
        "network_status": _network_status(ctx),
        "security_boundary": "user_code_not_sandboxed",
    }


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
    parent: dict[str, Any] | None = None
    parent_frame = None
    if payload.parent_dataset_version_id:
        parent = ctx.catalog.require("dataset_versions", payload.parent_dataset_version_id)
        if parent["project_id"] != record["project_id"]:
            raise ValueError("CROSS_PROJECT_NOTEBOOK_OUTPUT_FORBIDDEN")
        if record.get("dataset_version_id") and parent["id"] != record["dataset_version_id"]:
            raise ValueError("NOTEBOOK_PARENT_VERSION_MISMATCH")
        parents.append(parent["id"])
        parent_frame = ctx.catalog.dataset_frame(parent["id"])
        validation["inflation_ratio"] = len(frame) / max(int(parent["rows"]), 1)
        if payload.expected_grain == "same_or_fewer_rows" and validation["inflation_ratio"] > 1.001:
            raise ValueError("NOTEBOOK_OUTPUT_SAMPLE_INFLATION")
    if payload.customer_key:
        if payload.customer_key not in frame:
            raise ValueError("CUSTOMER_KEY_NOT_FOUND")
        validation["grain"] = (
            "customer" if frame[payload.customer_key].is_unique else "order_or_event"
        )
    output_profile = profile_frame(frame)
    protected_targets = list((parent or {}).get("profile", {}).get("binary_candidates", []))
    if payload.target_column:
        protected_targets = list(dict.fromkeys([*protected_targets, payload.target_column]))
    target_columns = protected_targets or list(output_profile["binary_candidates"])
    if target_columns and parent is None:
        raise ValueError("NOTEBOOK_PARENT_DATASET_REQUIRED_FOR_TARGET_LINEAGE")
    missing_targets = sorted(set(target_columns) - set(frame.columns))
    if missing_targets:
        raise ValueError(f"NOTEBOOK_OUTPUT_TARGET_COLUMN_DROPPED: {missing_targets}")
    validation["target_checks"] = {}
    for target_column in target_columns:
        diagnostics = diagnose_frame(frame, target_column)
        validation["target_checks"][target_column] = diagnostics["target"]
        blocking = [item for item in diagnostics["issues"] if item.get("severity") == "blocking"]
        if blocking:
            raise ValueError(blocking[0]["code"])
    if parent_frame is not None and target_columns:
        missing_in_parent = sorted(set(target_columns) - set(parent_frame.columns))
        if missing_in_parent:
            raise ValueError(f"NOTEBOOK_PARENT_TARGET_NOT_FOUND: {missing_in_parent}")
        requested_keys = list(
            dict.fromkeys(
                payload.key_columns or ([payload.customer_key] if payload.customer_key else [])
            )
        )
        key_columns = requested_keys or _infer_lineage_keys(parent or {}, parent_frame, frame)
        if not key_columns:
            raise ValueError("NOTEBOOK_TARGET_LINEAGE_KEYS_REQUIRED")
        lineage = _validate_target_lineage(
            parent_frame,
            frame,
            key_columns,
            target_columns,
            allow_subset=payload.expected_grain == "same_or_fewer_rows",
        )
        validation["target_lineage"] = lineage
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


def _infer_lineage_keys(parent: dict[str, Any], parent_frame: Any, output_frame: Any) -> list[str]:
    candidates = [
        str(item.get("name"))
        for item in (parent.get("profile") or {}).get("columns_detail", [])
        if item.get("id_candidate")
    ]
    preferred = sorted(
        candidates,
        key=lambda name: (
            0 if name.lower() in {"order_id", "application_id", "loan_id", "record_id"} else 1,
            name,
        ),
    )
    for column in preferred:
        if column not in parent_frame or column not in output_frame:
            continue
        if (
            parent_frame[column].notna().all()
            and output_frame[column].notna().all()
            and parent_frame[column].is_unique
            and output_frame[column].is_unique
        ):
            return [column]
    return []


def _validate_target_lineage(
    parent: Any,
    output: Any,
    key_columns: list[str],
    target_columns: list[str],
    *,
    allow_subset: bool,
) -> dict[str, Any]:
    missing = sorted(
        (set(key_columns) | set(target_columns)) - (set(parent.columns) & set(output.columns))
    )
    if missing:
        raise ValueError(f"NOTEBOOK_TARGET_LINEAGE_COLUMN_MISSING: {missing}")
    if parent[key_columns].isna().any(axis=None) or output[key_columns].isna().any(axis=None):
        raise ValueError("NOTEBOOK_TARGET_LINEAGE_KEY_MISSING")
    if parent.duplicated(key_columns).any() or output.duplicated(key_columns).any():
        raise ValueError("NOTEBOOK_TARGET_LINEAGE_KEY_NOT_UNIQUE")

    def mapping(frame: Any) -> dict[tuple[str, ...], tuple[str, ...]]:
        result: dict[tuple[str, ...], tuple[str, ...]] = {}
        for values in frame[[*key_columns, *target_columns]].itertuples(index=False, name=None):
            key = tuple(_canonical_lineage_value(value) for value in values[: len(key_columns)])
            targets = tuple(_canonical_target_value(value) for value in values[len(key_columns) :])
            result[key] = targets
        return result

    before = mapping(parent)
    after = mapping(output)
    if not allow_subset and set(before) != set(after):
        raise ValueError("NOTEBOOK_OUTPUT_KEY_SET_CHANGED")
    if not set(after).issubset(before):
        raise ValueError("NOTEBOOK_OUTPUT_NEW_BUSINESS_KEYS")
    changed = [key for key, value in after.items() if before[key] != value]
    if changed:
        raise ValueError("NOTEBOOK_OUTPUT_TARGET_MAPPING_CHANGED")
    digest_payload = [[*key, *after[key]] for key in sorted(after)]
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "key_columns": key_columns,
        "target_columns": target_columns,
        "parent_rows": len(before),
        "output_rows": len(after),
        "mapping_sha256": digest,
        "mapping_preserved": True,
    }


def _canonical_lineage_value(value: Any) -> str:
    if isinstance(value, bool):
        return f"bool:{int(value)}"
    if isinstance(value, numbers.Number):
        try:
            return f"number:{Decimal(str(value)).normalize()}"
        except InvalidOperation:
            pass
    if isinstance(value, (pd.Timestamp,)):
        return f"datetime:{value.isoformat()}"
    return f"text:{str(value).strip()}"


def _canonical_target_value(value: Any) -> str:
    normalized = normalize_binary(value)
    if normalized in {0.0, 1.0}:
        return str(int(normalized))
    try:
        if value is None or bool(pd.isna(value)):
            return "<MISSING>"
    except (TypeError, ValueError):
        pass
    return str(value).strip()
