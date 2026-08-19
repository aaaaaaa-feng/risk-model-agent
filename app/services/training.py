"""Approved-plan execution and immutable run artifact creation."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import joblib
import pandas as pd
import sklearn

from app import __version__
from app.db import Database, utc_now
from app.domain import DomainError
from app.ml import train_candidates
from app.services.planning import hash_plan
from app.services.profiling import load_csv
from app.services.reporting import BOUNDARY, render_model_report
from app.services.storage import Storage


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{timestamp}_{uuid4().hex[:8]}"


def _artifact_name(run_id: str, suffix: str) -> str:
    return f"{run_id}.{suffix}"


def _atomic_joblib_dump(model: object, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        joblib.dump(model, temporary_path)
        os.replace(str(temporary_path), str(destination))
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def execute_training(
    project_id: str,
    database: Database,
    storage: Storage,
    max_rows: int,
) -> str:
    """Execute one immutable run and return its ID.

    A failed run is retained as failed metadata, but it never receives a result
    artifact and the project view will not reuse metrics from an older run.
    """

    project = database.get_project(project_id)
    if project["status"] not in {"approved", "failed"}:
        raise DomainError(
            409,
            "PLAN_NOT_APPROVED",
            "Training requires a currently approved plan.",
            {"status": project["status"]},
        )
    if not project.get("approved_plan_hash"):
        raise DomainError(409, "APPROVAL_MISSING", "No approval hash is recorded.")

    plan = storage.read_json(project_id, "plan.json")
    current_hash = hash_plan(plan)
    if current_hash != project["approved_plan_hash"] or current_hash != plan.get("plan_hash"):
        raise DomainError(
            409,
            "APPROVED_PLAN_CHANGED",
            "The current plan no longer matches the approved snapshot.",
        )

    run_id = _run_id()
    previous_status = project["status"]
    database.create_run(run_id, project_id, current_hash)
    database.update_project(project_id, status="training", latest_run_id=run_id)
    database.add_event(
        project_id,
        "training_started",
        previous_status,
        "training",
        {"run_id": run_id, "plan_hash": current_hash},
    )

    try:
        dataframe = load_csv(storage.dataset_path(project_id), max_rows=max_rows)
        result, model = train_candidates(dataframe, plan)
        result.update(
            {
                "run_id": run_id,
                "project_id": project_id,
                "dataset_sha256": project.get("dataset_sha256"),
                "plan_hash": current_hash,
                "evidence_scope": "deterministic_local_worker",
                "dataset_is_demo": bool(project.get("dataset_is_demo")),
                "completed_at": utc_now(),
                "boundary": BOUNDARY,
            }
        )
        reproducibility = result.setdefault("reproducibility", {})
        reproducibility.update(
            {
                "app_version": __version__,
                "python": platform.python_version(),
                "pandas": pd.__version__,
                "scikit_learn": sklearn.__version__,
                "platform": sys.platform,
                "dataset_sha256": project.get("dataset_sha256"),
                "approved_plan_sha256": current_hash,
            }
        )

        project_dir = storage.project_dir(project_id)
        model_path = project_dir / _artifact_name(run_id, "model.joblib")
        _atomic_joblib_dump(model, model_path)
        result_path = storage.write_json(project_id, _artifact_name(run_id, "result.json"), result)

        report_project = {
            "name": project["name"],
            "dataset_is_demo": bool(project.get("dataset_is_demo")),
            "dataset_sha256": project.get("dataset_sha256"),
            "dataset": {"sha256": project.get("dataset_sha256")},
        }
        report_html = render_model_report(report_project, plan, result)
        report_path = project_dir / _artifact_name(run_id, "report.html")
        report_path.write_text(report_html, encoding="utf-8")

        database.update_run(
            run_id,
            status="completed",
            result_path=str(result_path),
            model_path=str(model_path),
            report_path=str(report_path),
            completed_at=result["completed_at"],
        )
        database.update_project(project_id, status="completed", latest_run_id=run_id)
        database.add_event(
            project_id,
            "training_completed",
            "training",
            "completed",
            {
                "run_id": run_id,
                "plan_hash": current_hash,
                "champion": (result.get("champion") or {}).get("name"),
            },
        )
        return run_id
    except DomainError:
        database.update_run(
            run_id,
            status="failed",
            error_message="Controlled training failure; see API response.",
            completed_at=utc_now(),
        )
        database.update_project(project_id, status="failed", latest_run_id=run_id)
        database.add_event(
            project_id,
            "training_failed",
            "training",
            "failed",
            {"run_id": run_id, "error_type": "domain_error"},
        )
        raise
    except Exception as exc:
        safe_message = str(exc)[:500]
        database.update_run(
            run_id,
            status="failed",
            error_message=safe_message,
            completed_at=utc_now(),
        )
        database.update_project(project_id, status="failed", latest_run_id=run_id)
        database.add_event(
            project_id,
            "training_failed",
            "training",
            "failed",
            {"run_id": run_id, "error_type": type(exc).__name__},
        )
        raise DomainError(
            422,
            "TRAINING_FAILED",
            "The approved local training run failed.",
            {"reason": safe_message},
        ) from exc
