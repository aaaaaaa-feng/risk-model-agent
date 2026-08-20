from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.core.database import JSON_COLUMNS, Database, new_id, now_iso
from app.core.paths import AppPaths, get_paths
from app.core.security import decrypt_file_payload, encrypt_file_payload, sha256_file

from .catalog import CatalogService, serialize_project_resources


RESOURCE_ORDER = (
    "data_assets",
    "dataset_versions",
    "join_plans",
    "target_tasks",
    "runs",
    "checkpoints",
    "review_records",
    "model_versions",
    "artifacts",
    "conversations",
    "conversation_messages",
    "conversation_events",
    "message_feedback",
    "decisions",
    "events",
    "notebooks",
    "score_jobs",
)


class ArchiveService:
    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
        catalog: CatalogService | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.catalog = catalog or CatalogService(self.database, self.paths)

    def create(self, project_id: str, password: str) -> tuple[dict[str, Any], str]:
        project = self.catalog.get_project(project_id)
        identifier = new_id("arc")
        destination = self.paths.archives / f"{identifier}.rma"
        with tempfile.TemporaryDirectory(prefix="risk-agent-archive-") as temporary:
            work = Path(temporary)
            inner = work / "project.zip"
            project_payload = serialize_project_resources(self.database, project_id)
            project_dir = self.paths.project_dir(project_id)
            with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr(
                    "project.json",
                    json.dumps(project_payload, ensure_ascii=False, indent=2, default=str),
                )
                if project_dir.exists():
                    for path in sorted(project_dir.rglob("*")):
                        if path.is_file():
                            archive.write(path, arcname=f"files/{path.relative_to(project_dir).as_posix()}")
            encrypted = work / "payload.bin"
            manifest, recovery_key = encrypt_file_payload(inner, encrypted, password)
            manifest.update(
                archive_id=identifier,
                project_id=project_id,
                project_name=project["name"],
                created_at=now_iso(),
                credentials_included=False,
            )
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as outer:
                outer.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                outer.write(encrypted, "payload.bin")
        record = self.database.insert(
            "archives",
            {
                "id": identifier,
                "project_id": project_id,
                "name": f"{project['name']}-{identifier}.rma",
                "path": str(destination),
                "checksum": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
                "status": "ready",
                "metadata_json": {"cipher": "AES-256-GCM", "recovery_key_shown_once": True},
                "created_at": now_iso(),
            },
        )
        return record, recovery_key

    def inspect(self, path: Path) -> dict[str, Any]:
        with zipfile.ZipFile(path) as archive:
            if set(archive.namelist()) != {"manifest.json", "payload.bin"}:
                raise ValueError("ARCHIVE_CONTAINER_INVALID")
            manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != "risk-encrypted-archive/v1":
            raise ValueError("ARCHIVE_SCHEMA_UNSUPPORTED")
        return manifest

    def restore(self, path: Path, credential: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="risk-agent-restore-") as temporary:
            work = Path(temporary)
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                encrypted = work / "payload.bin"
                with archive.open("payload.bin") as source, encrypted.open("wb") as target:
                    shutil.copyfileobj(source, target, 4 * 1024 * 1024)
            inner = decrypt_file_payload(encrypted, work / "project.zip", manifest, credential)
            extract_dir = work / "contents"
            extract_dir.mkdir()
            with zipfile.ZipFile(inner) as archive:
                _safe_extract(archive, extract_dir)
            payload = json.loads((extract_dir / "project.json").read_text(encoding="utf-8"))
            return self._import_payload(payload, extract_dir / "files")

    def _import_payload(self, payload: dict[str, Any], files_dir: Path) -> dict[str, Any]:
        original = payload.get("project") or {}
        restored = self.catalog.create_project(
            f"{original.get('name', '恢复项目')} · 恢复",
            str(original.get("description") or ""),
            str(original.get("mode") or "semi_trusted"),
            {"restored_from_project_id": original.get("id"), "archive_schema": payload.get("schema_version")},
        )
        destination = self.paths.project_dir(restored["id"])
        if files_dir.exists():
            shutil.copytree(files_dir, destination, dirs_exist_ok=True)
        old_project_dir = _common_old_project_dir(payload)
        id_map: dict[str, str] = {str(original.get("id")): restored["id"]}
        prefixes = {
            "data_assets": "asset", "dataset_versions": "dsv", "join_plans": "join",
            "target_tasks": "target", "runs": "run", "checkpoints": "chk",
            "review_records": "rev", "model_versions": "model", "artifacts": "art",
            "conversations": "conv", "conversation_messages": "msg", "decisions": "decision",
            "conversation_events": "cevt", "message_feedback": "feedback", "events": "evt", "notebooks": "nb", "score_jobs": "score",
        }
        for table in RESOURCE_ORDER:
            for row in payload.get(table, []):
                id_map[str(row["id"])] = new_id(prefixes[table])
        for table in RESOURCE_ORDER:
            for raw in payload.get(table, []):
                row = _remap_row(dict(raw), id_map, old_project_dir, destination)
                row["id"] = id_map[str(raw["id"])]
                if "project_id" in row:
                    row["project_id"] = restored["id"]
                try:
                    self.database.insert(table, _encode_json_fields(row))
                except (ValueError, KeyError):
                    raise
                except Exception as exc:
                    raise ValueError(f"ARCHIVE_RESOURCE_IMPORT_FAILED: {table}") from exc
        return self.catalog.get_project(restored["id"])


class BackupService:
    def __init__(self, database: Database | None = None, paths: AppPaths | None = None):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)

    def create(self, kind: str = "manual", source: str = "active_database") -> dict[str, Any]:
        identifier = new_id("bak")
        destination = self.paths.backups / f"{identifier}.sqlite3"
        self.database.backup(destination)
        return self.database.insert(
            "backups",
            {
                "id": identifier,
                "kind": kind,
                "source": source,
                "path": str(destination),
                "checksum": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
                "metadata_json": {"schema_version": 1},
                "created_at": now_iso(),
            },
        )

    def verify(self, backup_id: str) -> dict[str, Any]:
        record = self.database.get("backups", backup_id)
        if not record:
            raise KeyError(backup_id)
        path = Path(record["path"])
        valid = path.exists() and sha256_file(path) == record["checksum"]
        return {"id": backup_id, "valid": valid, "size_bytes": path.stat().st_size if path.exists() else 0}

    def restore(self, backup_id: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ValueError("BACKUP_RESTORE_CONFIRMATION_REQUIRED")
        verification = self.verify(backup_id)
        if not verification["valid"]:
            raise ValueError("BACKUP_CHECKSUM_MISMATCH")
        active = [
            run
            for run in self.database.list("runs", limit=5000)
            if run["status"] in {"queued", "running", "awaiting_decision"}
        ]
        if active:
            raise ValueError("BACKUP_RESTORE_BLOCKED_BY_ACTIVE_RUN")
        record = self.database.get("backups", backup_id)
        if not record:
            raise KeyError(backup_id)
        emergency_id = new_id("bak")
        emergency = self.paths.backups / f"pre-restore-{emergency_id}.sqlite3"
        self.database.backup(emergency)
        self.database.restore_from_backup(Path(record["path"]))
        if not self.database.get("backups", backup_id):
            self.database.insert(
                "backups",
                {
                    "id": record["id"],
                    "kind": record["kind"],
                    "source": record["source"],
                    "path": record["path"],
                    "checksum": record["checksum"],
                    "size_bytes": record["size_bytes"],
                    "metadata_json": record.get("metadata", {}),
                    "created_at": record["created_at"],
                },
            )
        self.database.insert(
            "backups",
            {
                "id": emergency_id,
                "kind": "pre_restore",
                "source": backup_id,
                "path": str(emergency),
                "checksum": sha256_file(emergency),
                "size_bytes": emergency.stat().st_size,
                "metadata_json": {"schema_version": 1, "recoverable": True},
                "created_at": now_iso(),
            },
        )
        return {
            "restored": True,
            "backup_id": backup_id,
            "emergency_backup": str(emergency),
            "emergency_backup_id": emergency_id,
            "restart_recommended": True,
        }


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError("ARCHIVE_PATH_TRAVERSAL")
    archive.extractall(destination)


def _common_old_project_dir(payload: dict[str, Any]) -> Path | None:
    paths: list[Path] = []
    for table in ("data_assets", "dataset_versions", "artifacts", "notebooks", "score_jobs"):
        for row in payload.get(table, []):
            for key in ("stored_path", "path", "output_path"):
                if row.get(key):
                    paths.append(Path(str(row[key])))
    project_id = str((payload.get("project") or {}).get("id") or "")
    for path in paths:
        parts = list(path.parts)
        if project_id in parts:
            return Path(*parts[: parts.index(project_id) + 1])
    return None


def _remap_row(
    row: dict[str, Any], id_map: dict[str, str], old_root: Path | None, new_root: Path
) -> dict[str, Any]:
    for key, value in list(row.items()):
        if key.endswith("_id") and value is not None and str(value) in id_map:
            row[key] = id_map[str(value)]
        elif key in {"parent_ids", "parent_ids_json"} and isinstance(value, list):
            row[key] = [id_map.get(str(item), str(item)) for item in value]
        elif key in {"steps", "steps_json"} and isinstance(value, list):
            row[key] = [
                {**item, "right_asset_id": id_map.get(str(item.get("right_asset_id")), item.get("right_asset_id"))}
                for item in value
            ]
        elif key in {"stored_path", "path", "output_path", "artifact_path"} and value and old_root:
            original = Path(str(value))
            try:
                row[key] = str(new_root / original.relative_to(old_root))
            except ValueError:
                row[key] = str(value)
    return row


def _encode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in list(result):
        json_key = f"{key}_json"
        if json_key in JSON_COLUMNS and key not in {"id"}:
            result[json_key] = result.pop(key)
    return result
