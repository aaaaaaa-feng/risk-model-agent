"""FastAPI routes for the governed local modeling workflow."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

from app.agent import build_agent_response
from app.db import Database, utc_now
from app.domain import DomainError
from app.services.planning import build_plan, hash_plan, validate_approval
from app.services.profiling import load_csv, profile_dataframe
from app.services.sample_data import SAMPLE_FILENAME, generate_sample_csv
from app.services.storage import Storage
from app.services.training import execute_training

router = APIRouter()


class PlanPayload(BaseModel):
    target_column: str = Field(min_length=1, max_length=200)
    positive_label: Any
    negative_label: Optional[Any] = None
    time_column: Optional[str] = Field(default=None, max_length=200)
    excluded_columns: List[str] = Field(default_factory=list, max_length=500)

    @field_validator("excluded_columns")
    @classmethod
    def unique_columns(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))


class ApprovalPayload(BaseModel):
    plan_version: int = Field(ge=1)
    plan_hash: str = Field(min_length=64, max_length=64)
    confirmations: List[str] = Field(default_factory=list, max_length=30)


class AgentPayload(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def _services(request: Request) -> tuple:
    return request.app.state.database, request.app.state.storage, request.app.state.settings


def _read_optional_json(
    storage: Storage, project_id: str, filename: str
) -> Optional[Dict[str, Any]]:
    try:
        value = storage.read_json(project_id, filename)
    except DomainError as exc:
        if exc.status_code == 404:
            return None
        raise
    return value if isinstance(value, dict) else None


def _run_view(storage: Storage, run: Dict[str, Any]) -> Dict[str, Any]:
    result = None
    if run.get("status") == "completed":
        result = _read_optional_json(storage, run["project_id"], f"{run['id']}.result.json")
    return {
        "id": run["id"],
        "status": run["status"],
        "plan_hash": run["plan_hash"],
        "error_message": run.get("error_message"),
        "created_at": run["created_at"],
        "completed_at": run.get("completed_at"),
        "result": result,
        "report_url": (
            f"/api/projects/{run['project_id']}/runs/{run['id']}/report"
            if run.get("report_path")
            else None
        ),
    }


def project_view(database: Database, storage: Storage, project_id: str) -> Dict[str, Any]:
    try:
        project = database.get_project(project_id)
    except KeyError as exc:
        raise DomainError(404, "PROJECT_NOT_FOUND", "Project was not found.") from exc
    profile = _read_optional_json(storage, project_id, "profile.json")
    plan = _read_optional_json(storage, project_id, "plan.json")
    runs = [_run_view(storage, run) for run in database.list_runs(project_id)]
    latest = next((run for run in runs if run["id"] == project.get("latest_run_id")), None)
    return {
        "id": project["id"],
        "name": project["name"],
        "status": project["status"],
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
        "dataset": {
            "filename": project.get("dataset_filename"),
            "sha256": project.get("dataset_sha256"),
            "size_bytes": project.get("dataset_size"),
            "rows": project.get("dataset_rows"),
            "columns": project.get("dataset_columns"),
            "is_demo": bool(project.get("dataset_is_demo")),
        },
        "profile": profile,
        "plan": plan,
        "approval": (
            {
                "plan_hash": project.get("approved_plan_hash"),
                "approved_at": project.get("approved_at"),
            }
            if project.get("approved_plan_hash")
            else None
        ),
        "runs": runs,
        "latest_run": latest,
        "events": database.list_events(project_id, limit=100),
        "agent_mode": "deterministic_offline_assistant",
    }


def _semantic_plan(plan: Dict[str, Any]) -> str:
    comparable = deepcopy(plan)
    comparable.pop("version", None)
    comparable.pop("plan_hash", None)
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _apply_user_exclusions(plan: Dict[str, Any], excluded_columns: List[str]) -> Dict[str, Any]:
    """Apply explicit exclusions without destabilising automatic policy drops."""

    updated = deepcopy(plan)
    features = updated["features"]
    included = list(features.get("included_columns") or [])
    dropped = list(features.get("dropped_columns") or [])
    column_types = dict(features.get("column_types") or {})
    protected = {
        (updated.get("target") or {}).get("column"),
        (updated.get("split") or {}).get("time_column"),
    }
    explicit = sorted(
        {
            name
            for name in excluded_columns
            if isinstance(name, str) and name and name not in protected
        }
    )
    newly_excluded = []
    for name in explicit:
        if name in included:
            included.remove(name)
            column_types.pop(name, None)
            newly_excluded.append(name)
        if name not in dropped:
            dropped.append(name)
    features["included_columns"] = included
    features["dropped_columns"] = dropped
    features["column_types"] = column_types
    features["user_excluded_columns"] = explicit
    if newly_excluded:
        updated.setdefault("warnings", []).append(
            {
                "code": "FEATURES_EXCLUDED_BY_USER",
                "message": "Fields were excluded by the human plan author.",
                "columns": newly_excluded,
                "details": {"reason": "user_excluded"},
            }
        )
    if not included and not any(
        item.get("code") == "NO_ELIGIBLE_FEATURES"
        for item in updated.get("blocking_issues", [])
        if isinstance(item, dict)
    ):
        updated.setdefault("blocking_issues", []).append(
            {
                "code": "NO_ELIGIBLE_FEATURES",
                "message": "No eligible modeling features remain after exclusions.",
                "columns": [],
                "details": {},
            }
        )
    updated["plan_hash"] = hash_plan(updated)
    return updated


@router.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "risk-model-agent",
        "agent_mode": "deterministic_offline_assistant",
        "external_data_egress": False,
    }


@router.get("/api/sample.csv")
def sample_dataset() -> Response:
    return Response(
        generate_sample_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{SAMPLE_FILENAME}"',
            "X-Dataset-Provenance": "synthetic-demo-only",
        },
    )


@router.get("/api/projects")
def list_projects(request: Request) -> Dict[str, Any]:
    database, storage, _ = _services(request)
    projects = []
    for row in database.list_projects():
        projects.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "dataset_is_demo": bool(row.get("dataset_is_demo")),
            }
        )
    return {"projects": projects}


@router.post("/api/projects", status_code=201)
async def create_project(
    request: Request,
    name: str = Form(...),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    database, storage, settings = _services(request)
    project_name = name.strip()
    if not project_name or len(project_name) > 100:
        raise DomainError(422, "INVALID_PROJECT_NAME", "Project name must be 1-100 characters.")
    filename = Path(file.filename or "").name
    if filename != (file.filename or ""):
        raise DomainError(400, "UNSAFE_FILENAME", "Dataset filename must not contain a path.")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise DomainError(
            413,
            "DATASET_TOO_LARGE",
            "CSV exceeds the local V1 upload limit.",
            {"max_bytes": settings.max_upload_bytes},
        )

    project_id = "proj_" + uuid4().hex[:16]
    database.create_project(project_id, project_name, status="uploaded")
    database.add_event(project_id, "project_created", None, "uploaded", {})
    try:
        metadata = storage.save_dataset(project_id, filename, content)
        dataframe = load_csv(storage.dataset_path(project_id), max_rows=settings.max_rows)
        profile = profile_dataframe(dataframe)
        profile_path = storage.write_json(project_id, "profile.json", profile)
        is_demo = filename == SAMPLE_FILENAME
        database.update_project(
            project_id,
            status="profiled",
            dataset_filename=metadata["original_filename"],
            dataset_sha256=metadata["sha256"],
            dataset_size=metadata["size_bytes"],
            dataset_rows=profile["row_count"],
            dataset_columns=profile["column_count"],
            dataset_is_demo=int(is_demo),
            profile_path=str(profile_path),
        )
        database.add_event(
            project_id,
            "dataset_profiled",
            "uploaded",
            "profiled",
            {
                "dataset_sha256": metadata["sha256"],
                "rows": profile["row_count"],
                "columns": profile["column_count"],
                "is_demo": is_demo,
            },
        )
    except Exception:
        database.update_project(project_id, status="failed")
        database.add_event(project_id, "dataset_import_failed", "uploaded", "failed", {})
        raise
    return project_view(database, storage, project_id)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, request: Request) -> Dict[str, Any]:
    database, storage, _ = _services(request)
    return project_view(database, storage, project_id)


@router.post("/api/projects/{project_id}/plan")
def create_plan(project_id: str, payload: PlanPayload, request: Request) -> Dict[str, Any]:
    database, storage, settings = _services(request)
    try:
        project = database.get_project(project_id)
    except KeyError as exc:
        raise DomainError(404, "PROJECT_NOT_FOUND", "Project was not found.") from exc
    if project["status"] not in {
        "profiled",
        "awaiting_approval",
        "approved",
        "completed",
        "failed",
    }:
        raise DomainError(
            409,
            "INVALID_PROJECT_STATE",
            "A plan cannot be generated in the current project state.",
            {"status": project["status"]},
        )

    profile = storage.read_json(project_id, "profile.json")
    dataframe = load_csv(storage.dataset_path(project_id), max_rows=settings.max_rows)
    planning_request: Dict[str, Any] = {
        "target_column": payload.target_column,
        "positive_label": payload.positive_label,
        "time_column": payload.time_column,
    }
    if payload.negative_label is not None:
        planning_request["negative_label"] = payload.negative_label

    current_plan = _read_optional_json(storage, project_id, "plan.json")
    current_version = int(project.get("plan_version") or 0)
    comparison_version = max(current_version, 1)
    candidate = build_plan(dataframe, profile, planning_request, comparison_version)
    candidate = _apply_user_exclusions(candidate, payload.excluded_columns)
    if current_plan is not None and _semantic_plan(candidate) == _semantic_plan(current_plan):
        database.add_event(
            project_id,
            "plan_unchanged",
            project["status"],
            project["status"],
            {"plan_hash": current_plan.get("plan_hash")},
        )
        return project_view(database, storage, project_id)

    next_version = current_version + 1
    if candidate["version"] != next_version:
        candidate = build_plan(dataframe, profile, planning_request, next_version)
        candidate = _apply_user_exclusions(candidate, payload.excluded_columns)
    plan_path = storage.write_json(project_id, "plan.json", candidate)
    previous_status = project["status"]
    database.update_project(
        project_id,
        status="awaiting_approval",
        plan_path=str(plan_path),
        plan_version=next_version,
        approved_plan_hash=None,
        approved_at=None,
        latest_run_id=None,
    )
    database.add_event(
        project_id,
        "plan_generated",
        previous_status,
        "awaiting_approval",
        {
            "version": next_version,
            "plan_hash": candidate["plan_hash"],
            "blocking_issue_count": len(candidate.get("blocking_issues") or []),
        },
    )
    return project_view(database, storage, project_id)


@router.post("/api/projects/{project_id}/approve")
def approve_plan(project_id: str, payload: ApprovalPayload, request: Request) -> Dict[str, Any]:
    database, storage, _ = _services(request)
    try:
        project = database.get_project(project_id)
    except KeyError as exc:
        raise DomainError(404, "PROJECT_NOT_FOUND", "Project was not found.") from exc
    if project["status"] != "awaiting_approval":
        raise DomainError(
            409,
            "PLAN_NOT_AWAITING_APPROVAL",
            "Only a pending plan can be approved.",
            {"status": project["status"]},
        )
    plan = storage.read_json(project_id, "plan.json")
    if payload.plan_version != plan.get("version"):
        raise DomainError(
            409, "PLAN_VERSION_MISMATCH", "Approval references an older plan version."
        )
    validate_approval(plan, payload.plan_hash, payload.confirmations)
    approved_at = utc_now()
    database.update_project(
        project_id,
        status="approved",
        approved_plan_hash=payload.plan_hash,
        approved_at=approved_at,
    )
    database.add_event(
        project_id,
        "plan_approved",
        "awaiting_approval",
        "approved",
        {
            "version": payload.plan_version,
            "plan_hash": payload.plan_hash,
            "confirmations": sorted(set(payload.confirmations)),
        },
    )
    return project_view(database, storage, project_id)


@router.post("/api/projects/{project_id}/train")
def train_project(project_id: str, request: Request) -> Dict[str, Any]:
    database, storage, settings = _services(request)
    execute_training(project_id, database, storage, settings.max_rows)
    return project_view(database, storage, project_id)


@router.post("/api/projects/{project_id}/agent")
def ask_agent(project_id: str, payload: AgentPayload, request: Request) -> Dict[str, Any]:
    database, storage, _ = _services(request)
    view = project_view(database, storage, project_id)
    database.add_event(
        project_id,
        "agent_consulted",
        view["status"],
        view["status"],
        {"mode": "deterministic_offline_assistant"},
    )
    return build_agent_response(view, payload.message)


@router.get("/api/projects/{project_id}/runs/{run_id}/report")
def download_report(project_id: str, run_id: str, request: Request) -> HTMLResponse:
    database, storage, _ = _services(request)
    try:
        run = database.get_run(run_id)
    except KeyError as exc:
        raise DomainError(404, "RUN_NOT_FOUND", "Run was not found.") from exc
    if run["project_id"] != project_id or not run.get("report_path"):
        raise DomainError(404, "REPORT_NOT_FOUND", "Report was not found for this project.")
    root = storage.project_dir(project_id, create=False)
    path = Path(run["report_path"]).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DomainError(400, "UNSAFE_REPORT_PATH", "Report path is outside the project.") from exc
    if not path.is_file():
        raise DomainError(404, "REPORT_NOT_FOUND", "Report artifact is missing.")
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}-report.html"',
            "Cache-Control": "no-store",
        },
    )
