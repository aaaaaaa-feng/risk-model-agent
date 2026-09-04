from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import SettingsStore
from app.core.database import Database, new_id, now_iso, snapshot_legacy_database
from app.core.paths import AppPaths, get_paths
from app.core.security import sha256_file
from app.providers.secrets import SecretStore


class LegacyMigrator:
    """One-way, non-destructive v0 importer.

    Compatible projects/datasets are registered in V1. Old runs remain available
    as read-only legacy records because their state machine is not V1-compatible.
    The source runtime is never deleted by this class.
    """

    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.marker = self.paths.root / "legacy-v0-migration.json"

    def migrate(self, legacy_runtime: Path) -> dict[str, Any]:
        legacy_runtime = legacy_runtime.expanduser().resolve()
        legacy_database = legacy_runtime / "risk_agent.sqlite3"
        if self.marker.exists():
            try:
                return json.loads(self.marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if not legacy_database.exists():
            return {"status": "not_found", "source": str(legacy_runtime)}
        registered = self.database.list(
            "backups",
            {"kind": "pre_upgrade_v0", "source": str(legacy_database)},
            limit=1,
        )
        registered_path = Path(registered[0]["path"]) if registered else None
        backup = (
            registered_path
            if registered_path is not None and registered_path.is_file()
            else snapshot_legacy_database(legacy_database, self.paths)
        )
        if backup is not None and not registered:
            self.database.insert(
                "backups",
                {
                    "id": new_id("bak"),
                    "kind": "pre_upgrade_v0",
                    "source": str(legacy_database),
                    "path": str(backup),
                    "checksum": sha256_file(backup),
                    "size_bytes": backup.stat().st_size,
                    "metadata_json": {"legacy_schema": "v0", "automatic": True},
                    "created_at": now_iso(),
                },
            )
        elif backup is not None and registered_path is not None and not registered_path.is_file():
            self.database.update(
                "backups",
                registered[0]["id"],
                {
                    "path": str(backup),
                    "checksum": sha256_file(backup),
                    "size_bytes": backup.stat().st_size,
                },
            )
        copied_root = self.paths.legacy / "runtime"
        copied_root.mkdir(parents=True, exist_ok=True)
        self._copy_runtime(legacy_runtime, copied_root)
        project_map: dict[str, str] = {}
        imported_projects = 0
        imported_datasets = 0
        imported_runs = 0
        with sqlite3.connect(legacy_database) as connection:
            connection.row_factory = sqlite3.Row
            for raw in connection.execute("SELECT * FROM projects ORDER BY created_at"):
                row = dict(raw)
                existing = self.database.list(
                    "legacy_records",
                    {"record_type": "project", "source_id": row["id"]},
                    limit=1,
                )
                if existing:
                    project_map[row["id"]] = str(
                        (existing[0].get("metadata") or {}).get("v1_project_id") or ""
                    )
                    continue
                identifier = new_id("prj")
                project_map[row["id"]] = identifier
                project_row = {
                    "id": identifier,
                    "name": row["name"],
                    "description": "由 V0 自动迁移；旧 Run 以只读记录保留。",
                    "status": "archived" if row.get("status") == "archived" else "active",
                    "mode": "semi_trusted",
                    "created_at": row["created_at"],
                    "updated_at": now_iso(),
                    "metadata_json": {"legacy_project_id": row["id"], "migrated_from": "v0"},
                    "legacy_readonly": False,
                }
                legacy_row = {
                    "id": new_id("legacy"),
                    "record_type": "project",
                    "source_id": row["id"],
                    "source_path": str(copied_root / "projects" / row["id"]),
                    "metadata_json": {"v1_project_id": identifier, "status": row.get("status")},
                    "created_at": now_iso(),
                }
                self.database.insert_many_atomic(
                    [("projects", project_row), ("legacy_records", legacy_row)]
                )
                imported_projects += 1
            for raw in connection.execute("SELECT * FROM datasets ORDER BY created_at"):
                row = dict(raw)
                existing_dataset = self.database.list(
                    "legacy_records",
                    {"record_type": "dataset", "source_id": row["id"]},
                    limit=1,
                )
                if existing_dataset:
                    continue
                project_id = project_map.get(row["project_id"])
                if not project_id:
                    legacy = self.database.list(
                        "legacy_records",
                        {"record_type": "project", "source_id": row["project_id"]},
                        limit=1,
                    )
                    project_id = (
                        (legacy[0].get("metadata") or {}).get("v1_project_id") if legacy else None
                    )
                if not project_id:
                    continue
                identifier = new_id("asset")
                dataset_version_id = new_id("dsv")
                source_path = _remap_legacy_path(Path(row["path"]), legacy_runtime, copied_root)
                if not source_path.exists():
                    self._legacy_record(
                        "dataset_missing", row["id"], source_path, {"project_id": project_id}
                    )
                    continue
                asset_row = {
                    "id": identifier,
                    "project_id": project_id,
                    "name": row["filename"],
                    "kind": "base",
                    "format": source_path.suffix.lower().lstrip("."),
                    "stored_path": str(source_path),
                    "sheet": row.get("sheet"),
                    "sha256": row["sha256"],
                    "size_bytes": row["bytes"],
                    "rows": row.get("rows"),
                    "columns": row.get("columns"),
                    "status": "ready",
                    "metadata_json": {
                        "legacy_dataset_id": row["id"],
                        "is_demo": bool(row.get("is_demo")),
                    },
                    "created_at": row["created_at"],
                }
                profile = _json(row.get("profile_json"), {})
                dataset_row = {
                    "id": dataset_version_id,
                    "project_id": project_id,
                    "label": f"{row['filename']} · V0 迁移",
                    "stored_path": str(source_path),
                    "format": source_path.suffix.lower().lstrip("."),
                    "sheet": row.get("sheet"),
                    "rows": int(row.get("rows") or profile.get("rows") or 0),
                    "columns": int(row.get("columns") or profile.get("columns") or 0),
                    "parent_ids_json": [identifier],
                    "lineage_json": {"kind": "legacy_v0", "legacy_dataset_id": row["id"]},
                    "profile_json": profile,
                    "is_frozen": True,
                    "created_at": row["created_at"],
                }
                legacy_row = {
                    "id": new_id("legacy"),
                    "record_type": "dataset",
                    "source_id": row["id"],
                    "source_path": str(source_path),
                    "metadata_json": {
                        "v1_asset_id": identifier,
                        "v1_dataset_version_id": dataset_version_id,
                        "v1_project_id": project_id,
                    },
                    "created_at": now_iso(),
                }
                self.database.insert_many_atomic(
                    [
                        ("data_assets", asset_row),
                        ("dataset_versions", dataset_row),
                        ("legacy_records", legacy_row),
                    ]
                )
                imported_datasets += 1
            for raw in connection.execute("SELECT * FROM runs ORDER BY created_at"):
                row = dict(raw)
                source_path = copied_root / "projects" / row["project_id"] / "runs" / row["id"]
                inserted = self._legacy_record(
                    "run",
                    row["id"],
                    source_path,
                    {
                        "v1_project_id": project_map.get(row["project_id"]),
                        "legacy_project_id": row["project_id"],
                        "legacy_dataset_id": row["dataset_id"],
                        "status": row["status"],
                        "phase": row["phase"],
                        "mode": row["mode"],
                        "state": _json(row.get("state_json"), {}),
                        "error": row.get("error"),
                        "created_at": row["created_at"],
                    },
                )
                imported_runs += int(inserted)
        self._migrate_public_config(legacy_runtime)
        self._migrate_secret(legacy_runtime)
        result = {
            "status": "completed",
            "source": str(legacy_runtime),
            "backup": str(backup) if backup else None,
            "copied_root": str(copied_root),
            "projects": imported_projects,
            "datasets": imported_datasets,
            "legacy_runs": imported_runs,
            "source_deleted": False,
            "completed_at": now_iso(),
        }
        marker_temporary = self.marker.with_suffix(".tmp")
        marker_temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        marker_temporary.replace(self.marker)
        return result

    @staticmethod
    def _copy_runtime(source: Path, destination: Path) -> None:
        for name in (
            "projects",
            "risk_agent.sqlite3",
            "risk_agent.sqlite3-wal",
            "risk_agent.sqlite3-shm",
        ):
            current = source / name
            if not current.exists():
                continue
            target = destination / name
            if current.is_dir():
                shutil.copytree(current, target, dirs_exist_ok=True)
            else:
                shutil.copy2(current, target)

    def _legacy_record(
        self,
        record_type: str,
        source_id: str,
        source_path: Path,
        metadata: dict[str, Any],
    ) -> bool:
        existing = self.database.list(
            "legacy_records", {"record_type": record_type, "source_id": source_id}, limit=1
        )
        if existing:
            return False
        self.database.insert(
            "legacy_records",
            {
                "id": new_id("legacy"),
                "record_type": record_type,
                "source_id": source_id,
                "source_path": str(source_path),
                "metadata_json": metadata,
                "created_at": now_iso(),
            },
        )
        return True

    def _migrate_public_config(self, legacy_runtime: Path) -> None:
        path = legacy_runtime / "app-config.json"
        if not path.exists() or self.paths.config.exists():
            return
        raw = _json(path.read_text(encoding="utf-8"), {})
        allowed = {
            "provider",
            "api_format",
            "base_url",
            "model",
            "reviewer_model",
            "llm_enabled",
            "mode",
            "run_token_budget",
            "monthly_token_budget",
            "proxy",
            "ca_cert",
            "telemetry",
            "auto_update",
            "memory_budget_mb",
            "max_parallel_models",
        }
        SettingsStore(self.paths).save({key: value for key, value in raw.items() if key in allowed})

    def _migrate_secret(self, legacy_runtime: Path) -> None:
        target = SecretStore(self.paths)
        if target.read():
            return
        candidates = [
            legacy_runtime / "secrets" / "provider_api_key",
            legacy_runtime / "secrets" / "api_key",
        ]
        for candidate in candidates:
            try:
                value = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                target.save(value)
                return


def _remap_legacy_path(path: Path, source_root: Path, copied_root: Path) -> Path:
    try:
        return copied_root / path.resolve().relative_to(source_root)
    except ValueError:
        project_marker = "projects"
        parts = list(path.parts)
        if project_marker in parts:
            return copied_root.joinpath(*parts[parts.index(project_marker) :])
        return copied_root / "external" / path.name


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback
