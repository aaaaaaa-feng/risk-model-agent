from __future__ import annotations

import importlib.metadata
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.database import Database, new_id, now_iso
from app.core.errors import normalize_error_code
from app.core.paths import AppPaths, get_paths
from app.core.security import sha256_file
from app.workers.binning import bin_report
from app.workers.io import read_table, write_table
from app.workers.model_package import build_model_package
from app.workers.modeling import ModelBundle
from app.workers.package_runtime import (
    safe_extract_model_package,
    score_package_directory,
    validate_frame_contract,
)
from app.workers.reporting import (
    build_report,
    write_report_excel,
    write_report_html,
    write_report_json,
)
from app.workers.scoring import append_scores

from .catalog import CatalogService


MIME_TYPES = {
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".ipynb": "application/x-ipynb+json",
    ".csv": "text/csv; charset=utf-8",
    ".skops": "application/octet-stream",
}


class ArtifactService:
    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
        catalog: CatalogService | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.catalog = catalog or CatalogService(self.database, self.paths)

    def run_dir(self, project_id: str, run_id: str) -> Path:
        path = self.paths.project_dir(project_id) / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def build_structured_report(
        self,
        run: dict[str, Any],
        state: dict[str, Any],
        frame: pd.DataFrame,
    ) -> dict[str, Any]:
        project = self.catalog.require("projects", run["project_id"])
        task = self.catalog.require("target_tasks", run["target_task_id"])
        dataset = self.catalog.require("dataset_versions", task["dataset_version_id"])
        target = task["target_column"]
        split = state["split"]
        indices = split["indices"]
        reports = {
            name: bin_report(frame.iloc[positions], target, state["binning"], name)
            for name, positions in indices.items()
            if positions
        }
        reviews = self.database.list(
            "review_records", {"run_id": run["id"]}, order_by="round ASC", limit=100
        )
        return build_report(
            project=project,
            run=run,
            target_task=task,
            dataset=dataset,
            diagnostics=state["diagnostics"],
            split=split,
            screening=state["screening"],
            binning=state["binning"],
            model_result=state["model_result"],
            bin_reports=reports,
            reviews=reviews,
            lineage=dataset.get("lineage", {}),
        )

    def write_report_artifacts(
        self, run: dict[str, Any], report: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        directory = self.run_dir(run["project_id"], run["id"])
        paths = [
            directory / "model-report.json",
            directory / "model-report.xlsx",
            directory / "model-report.html",
        ]
        declared = {
            (item.get("name"), item.get("kind")): item for item in report.get("artifacts", [])
        }
        for path in paths:
            declared[(path.name, _kind(path))] = {"name": path.name, "kind": _kind(path)}
        report["artifacts"] = list(declared.values())
        write_report_json(report, paths[0])
        write_report_excel(report, paths[1])
        write_report_html(report, paths[2])
        artifacts = [self.register(run["id"], _kind(path), path) for path in paths]
        return report, artifacts

    def write_model_artifacts(
        self,
        run: dict[str, Any],
        task: dict[str, Any],
        bundle: ModelBundle,
        frame: pd.DataFrame,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        directory = self.run_dir(run["project_id"], run["id"])
        model_name = f"{task['target_column']}-{bundle.algorithm}-{run['id'][-6:]}"
        bundle.name = model_name
        contract = {
            "schema_version": "risk-field-contract/v2",
            "model_name": model_name,
            "target_excluded": task["target_column"],
            "required_fields": bundle.features,
            "dtypes": {column: str(frame[column].dtype) for column in bundle.features},
            "field_types": {
                column: (
                    "numeric"
                    if pd.api.types.is_numeric_dtype(frame[column])
                    else "datetime"
                    if pd.api.types.is_datetime64_any_dtype(frame[column])
                    else "categorical"
                )
                for column in bundle.features
            },
            "missing_policy": "pipeline_imputation_or_woe_missing_bin",
            "unknown_category_policy": "ignore_or_other_bin",
            "score_config": bundle.score_config,
        }
        dependencies = _dependency_lock()
        package, manifest = build_model_package(
            bundle,
            contract,
            directory / f"{model_name}-model-package.zip",
            dependencies,
        )
        model_version = self.database.insert(
            "model_versions",
            {
                "id": new_id("model"),
                "run_id": run["id"],
                "target_task_id": task["id"],
                "name": model_name,
                "algorithm": bundle.algorithm,
                "status": "ready",
                "metrics_json": bundle.metrics,
                "artifact_path": str(package),
                "contract_json": contract,
                "checksum": sha256_file(package),
                "champion": True,
                "created_at": now_iso(),
            },
        )
        self.register(run["id"], "model_package", package, {"manifest": manifest})
        return model_version, manifest

    def score_file(
        self,
        model_version_id: str,
        input_asset_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        model = self.catalog.require("model_versions", model_version_id)
        asset = self.catalog.require("data_assets", input_asset_id)
        run = self.catalog.require("runs", model["run_id"])
        if asset["project_id"] != run["project_id"]:
            raise ValueError("CROSS_PROJECT_SCORING_FORBIDDEN")
        try:
            frame = read_table(Path(asset["stored_path"]), asset.get("sheet"))
        except Exception as exc:
            code = normalize_error_code(exc, "SCORE_INPUT_READ_FAILED")
            raise ValueError(code) from exc
        artifact_path = Path(model["artifact_path"])
        if not artifact_path.is_file() or sha256_file(artifact_path) != model["checksum"]:
            raise ValueError("MODEL_ARTIFACT_CHECKSUM_MISMATCH")
        if artifact_path.suffix.lower() == ".zip":
            with tempfile.TemporaryDirectory(prefix="risk-model-score-") as temporary:
                package_root = safe_extract_model_package(
                    artifact_path, Path(temporary) / "package"
                )
                probability, contract, score_config, manifest = score_package_directory(
                    package_root, frame
                )
        else:
            # Read-only compatibility for checksum-verified V1 model records.
            package_root = artifact_path.parent
            probability, contract, score_config, manifest = score_package_directory(
                package_root, frame, allow_legacy=True
            )
        if (
            manifest.get("model_name") != model["name"]
            or manifest.get("algorithm") != model["algorithm"]
            or contract != model["contract"]
            or score_config != dict(contract.get("score_config") or {})
        ):
            raise ValueError("MODEL_PACKAGE_DATABASE_CONTRACT_MISMATCH")
        validate_frame_contract(frame, contract)
        scored, evidence = append_scores(
            frame,
            probability,
            model["name"],
            score_config,
        )
        identifier = new_id("score")
        destination = self.paths.project_dir(run["project_id"]) / "scores" / f"{identifier}.csv"
        write_table(scored, destination)
        output_evidence = {**evidence, "output_sha256": sha256_file(destination)}
        artifact = self.register(run["id"], "score_output", destination, output_evidence)
        output_evidence["artifact_id"] = artifact["id"]
        timestamp = now_iso()
        job = self.database.insert(
            "score_jobs",
            {
                "id": identifier,
                "project_id": run["project_id"],
                "model_version_id": model_version_id,
                "input_asset_id": input_asset_id,
                "status": "succeeded",
                "output_path": str(destination),
                "rows": len(scored),
                "metadata_json": output_evidence,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        return job, artifact

    def register(
        self,
        run_id: str,
        kind: str,
        path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.database.insert(
            "artifacts",
            {
                "id": new_id("art"),
                "run_id": run_id,
                "kind": kind,
                "name": path.name,
                "path": str(path),
                "mime_type": MIME_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                "checksum": sha256_file(path),
                "metadata_json": metadata or {},
                "created_at": now_iso(),
            },
        )

    def _refresh(self, artifact: dict[str, Any], path: Path) -> dict[str, Any]:
        return self.database.update("artifacts", artifact["id"], {"checksum": sha256_file(path)})


def _kind(path: Path) -> str:
    return {".json": "report_json", ".xlsx": "report_excel", ".html": "report_html"}[path.suffix]


def _dependency_lock() -> list[str]:
    names = [
        "python",
        "numpy",
        "pandas",
        "scikit-learn",
        "skops",
        "openpyxl",
        "xgboost",
        "lightgbm",
        "catboost",
    ]
    values = [f"python=={__import__('platform').python_version()}"]
    for name in names[1:]:
        try:
            values.append(f"{name}=={importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            continue
    return values


def _score_config(model: dict[str, Any]) -> dict[str, float]:
    return dict((model.get("contract") or {}).get("score_config") or {})
