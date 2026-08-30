from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import MAX_ARCHIVE_BYTES
from app.core.database import new_id
from app.core.security import sha256_file
from app.bootstrap import AppContext

from .dependencies import context


router = APIRouter(tags=["reports-models-scoring-archives"])


class ScoreJobCreate(BaseModel):
    model_version_id: str
    input_asset_id: str


class ArchiveCreate(BaseModel):
    password: str = Field(min_length=10, max_length=256)


class BackupRestore(BaseModel):
    confirm: bool


@router.get("/reports/{run_id}")
def get_report(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    artifact = _artifact(ctx, run_id, "report_json")
    path = _verified_path(artifact)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("REPORT_READ_FAILED") from exc


@router.get("/reports/{run_id}/html")
def report_html(run_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    return _file(_artifact(ctx, run_id, "report_html"))


@router.get("/reports/{run_id}/excel")
def report_excel(run_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    return _file(_artifact(ctx, run_id, "report_excel"))


@router.get("/runs/{run_id}/artifacts")
def list_artifacts(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    return {"artifacts": ctx.database.list("artifacts", {"run_id": run_id}, limit=5000)}


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    return _file(ctx.catalog.require("artifacts", artifact_id))


@router.get("/projects/{project_id}/models")
def list_models(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    runs = ctx.database.list("runs", {"project_id": project_id}, limit=5000)
    models = [
        model
        for run in runs
        for model in ctx.database.list("model_versions", {"run_id": run["id"]}, limit=5000)
    ]
    return {"models": models}


@router.get("/models/{model_version_id}")
def get_model(model_version_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"model": ctx.catalog.require("model_versions", model_version_id)}


@router.post("/score-jobs", status_code=201)
def create_score_job(payload: ScoreJobCreate, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    job, artifact = ctx.engine.worker.score_file(payload.model_version_id, payload.input_asset_id)
    return {"score_job": job, "artifact": artifact}


@router.get("/projects/{project_id}/score-jobs")
def list_score_jobs(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"score_jobs": ctx.database.list("score_jobs", {"project_id": project_id}, limit=5000)}


@router.get("/score-jobs/{job_id}/download")
def download_score_job(job_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    job = ctx.catalog.require("score_jobs", job_id)
    path = Path(job["output_path"])
    expected = (job.get("metadata") or {}).get("output_sha256")
    if not path.exists() or not expected or sha256_file(path) != expected:
        raise ValueError("SCORE_OUTPUT_CHECKSUM_MISMATCH")
    return FileResponse(path, filename=path.name, media_type="text/csv; charset=utf-8")


@router.post("/projects/{project_id}/archives", status_code=201)
def create_archive(
    project_id: str, payload: ArchiveCreate, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    archive, recovery_key = ctx.archives.create(project_id, payload.password)
    return {
        "archive": archive,
        "recovery_key": recovery_key,
        "warning": "恢复密钥只显示这一次；请离线保存。",
    }


@router.get("/projects/{project_id}/archives")
def list_archives(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"archives": ctx.database.list("archives", {"project_id": project_id}, limit=5000)}


@router.get("/archives/{archive_id}/download")
def download_archive(archive_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    return _file(ctx.catalog.require("archives", archive_id))


@router.post("/archives/restore", status_code=201)
async def restore_archive(
    file: UploadFile = File(...),
    credential: str = Form(...),
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    incoming = ctx.paths.root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / f"{new_id('restore')}.rma"
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("ARCHIVE_SIZE_LIMIT_EXCEEDED")
                target.write(chunk)
        manifest = ctx.archives.inspect(path)
        project = ctx.archives.restore(path, credential)
        return {"project": project, "manifest": manifest}
    finally:
        path.unlink(missing_ok=True)
        await file.close()


@router.post("/backups", status_code=201)
def create_backup(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"backup": ctx.backups.create()}


@router.get("/backups")
def list_backups(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"backups": ctx.database.list("backups", limit=5000)}


@router.get("/backups/{backup_id}/verify")
def verify_backup(backup_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return ctx.backups.verify(backup_id)


@router.get("/backups/{backup_id}/download")
def download_backup(backup_id: str, ctx: AppContext = Depends(context)) -> FileResponse:
    record = ctx.catalog.require("backups", backup_id)
    path = Path(record["path"])
    if sha256_file(path) != record["checksum"]:
        raise ValueError("BACKUP_CHECKSUM_MISMATCH")
    return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")


@router.post("/backups/{backup_id}/restore")
def restore_backup(
    backup_id: str, payload: BackupRestore, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    return ctx.backups.restore(backup_id, payload.confirm)


def _artifact(ctx: AppContext, run_id: str, kind: str) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    values = ctx.database.list("artifacts", {"run_id": run_id, "kind": kind}, limit=20)
    if not values:
        raise KeyError(kind)
    return values[0]


def _file(record: dict[str, Any]) -> FileResponse:
    path = _verified_path(record)
    return FileResponse(path, filename=record["name"], media_type=record.get("mime_type"))


def _verified_path(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.exists():
        raise ValueError("ARTIFACT_FILE_MISSING")
    if sha256_file(path) != record["checksum"]:
        raise ValueError("ARTIFACT_CHECKSUM_MISMATCH")
    return path
