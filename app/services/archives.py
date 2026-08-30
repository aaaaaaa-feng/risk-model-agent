from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.core.database import JSON_COLUMNS, SCHEMA_VERSION, Database, new_id, now_iso
from app.core.config import MAX_ARCHIVE_BYTES
from app.core.paths import AppPaths, PROJECT_MARKER_FILE, get_paths
from app.core.security import decrypt_file_payload, encrypt_file_payload, sha256_file
from app.governance.manifest import canonical_hash

from .catalog import CatalogService, serialize_project_resources


RESOURCE_ORDER = (
    "data_assets",
    "dataset_versions",
    "join_plans",
    "target_tasks",
    "runs",
    "run_manifests",
    "traces",
    "trace_spans",
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
    "provider_requests",
    "notebooks",
    "score_jobs",
)

ARCHIVE_SCHEMA = "risk-project-export/v1"
MAX_ARCHIVE_MEMBERS = 100_000
MAX_PROJECT_JSON_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = int(os.getenv("RISK_AGENT_MAX_ARCHIVE_UNPACKED_MB", "16384")) * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
PATH_FIELDS = {"stored_path", "path", "output_path", "artifact_path", "generated_code_path"}
REQUIRED_REFERENCE_FIELDS = {
    "project_id",
    "asset_id",
    "base_asset_id",
    "right_asset_id",
    "dictionary_asset_id",
    "input_asset_id",
    "output_dataset_version_id",
    "dataset_version_id",
    "working_dataset_version_id",
    "target_task_id",
    "run_id",
    "conversation_id",
    "message_id",
    "model_version_id",
    "notebook_id",
    "join_plan_id",
    "decision_id",
    "artifact_id",
    "provider_request_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "root_span_id",
}
REQUIRED_REFERENCE_LIST_FIELDS = {"parent_ids", "artifact_ids"}
PREFIXES = {
    "data_assets": "asset",
    "dataset_versions": "dsv",
    "join_plans": "join",
    "target_tasks": "target",
    "runs": "run",
    "run_manifests": "manifest",
    "traces": "trace",
    "trace_spans": "span",
    "checkpoints": "chk",
    "review_records": "rev",
    "model_versions": "model",
    "artifacts": "art",
    "conversations": "conv",
    "conversation_messages": "msg",
    "conversation_events": "cevt",
    "message_feedback": "feedback",
    "decisions": "decision",
    "events": "evt",
    "provider_requests": "provider",
    "notebooks": "nb",
    "score_jobs": "score",
}


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
        self._cleanup_interrupted_restores()

    def create(self, project_id: str, password: str) -> tuple[dict[str, Any], str]:
        project = self.catalog.get_project(project_id)
        active_runs = [
            run
            for run in self.database.list_all("runs", {"project_id": project_id})
            if run["status"] in {"queued", "running", "awaiting_decision"}
        ]
        if active_runs:
            raise ValueError("ARCHIVE_CREATE_BLOCKED_BY_ACTIVE_RUN")
        identifier = new_id("arc")
        destination = self.paths.archives / f"{identifier}.rma"
        with tempfile.TemporaryDirectory(prefix="risk-agent-archive-") as temporary:
            work = Path(temporary)
            inner = work / "project.zip"
            project_payload = serialize_project_resources(self.database, project_id)
            _validate_project_payload(project_payload)
            serialized_project = json.dumps(
                project_payload, ensure_ascii=False, indent=2, default=str
            ).encode("utf-8")
            if len(serialized_project) > MAX_PROJECT_JSON_BYTES:
                raise ValueError("ARCHIVE_PROJECT_MANIFEST_SIZE_LIMIT_EXCEEDED")
            project_dir = self.paths.project_dir(project_id)
            with zipfile.ZipFile(
                inner, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                archive.writestr(
                    "project.json",
                    serialized_project,
                )
                if project_dir.exists():
                    project_root = project_dir.resolve()
                    for path in sorted(project_dir.rglob("*")):
                        if path.is_symlink():
                            raise ValueError("ARCHIVE_PROJECT_SYMLINK_FORBIDDEN")
                        if path.is_file():
                            resolved = path.resolve()
                            if project_root not in resolved.parents:
                                raise ValueError("ARCHIVE_PROJECT_PATH_OUTSIDE_ROOT")
                            archive.write(
                                path, arcname=f"files/{path.relative_to(project_dir).as_posix()}"
                            )
            encrypted = work / "payload.bin"
            manifest, recovery_key = encrypt_file_payload(inner, encrypted, password)
            manifest.update(
                archive_id=identifier,
                project_id=project_id,
                project_name=project["name"],
                created_at=now_iso(),
                credentials_included=False,
            )
            with zipfile.ZipFile(
                destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as outer:
                outer.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                outer.write(encrypted, "payload.bin")
        if destination.stat().st_size > MAX_ARCHIVE_BYTES:
            destination.unlink(missing_ok=True)
            raise ValueError("ARCHIVE_SIZE_LIMIT_EXCEEDED")
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
        if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("ARCHIVE_SIZE_LIMIT_EXCEEDED")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if (
                len(names) != 2
                or len(names) != len(set(names))
                or set(names) != {"manifest.json", "payload.bin"}
            ):
                raise ValueError("ARCHIVE_CONTAINER_INVALID")
            manifest_info = archive.getinfo("manifest.json")
            payload_info = archive.getinfo("payload.bin")
            if manifest_info.file_size > 1024 * 1024 or payload_info.file_size > MAX_ARCHIVE_BYTES:
                raise ValueError("ARCHIVE_CONTAINER_SIZE_INVALID")
            manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != "risk-encrypted-archive/v1":
            raise ValueError("ARCHIVE_SCHEMA_UNSUPPORTED")
        _validate_encryption_manifest(manifest)
        return manifest

    def restore(self, path: Path, credential: str) -> dict[str, Any]:
        manifest = self.inspect(path)
        with tempfile.TemporaryDirectory(prefix="risk-agent-restore-") as temporary:
            work = Path(temporary)
            with zipfile.ZipFile(path) as archive:
                encrypted = work / "payload.bin"
                with archive.open("payload.bin") as source, encrypted.open("wb") as target:
                    shutil.copyfileobj(source, target, 4 * 1024 * 1024)
            inner = decrypt_file_payload(encrypted, work / "project.zip", manifest, credential)
            extract_dir = work / "contents"
            extract_dir.mkdir()
            with zipfile.ZipFile(inner) as archive:
                _validate_inner_archive(archive)
                _safe_extract(archive, extract_dir)
            project_json = extract_dir / "project.json"
            if not project_json.is_file() or project_json.stat().st_size > MAX_PROJECT_JSON_BYTES:
                raise ValueError("ARCHIVE_PROJECT_MANIFEST_INVALID")
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            return self._import_payload(payload, extract_dir / "files")

    def _import_payload(self, payload: dict[str, Any], files_dir: Path) -> dict[str, Any]:
        _validate_project_payload(payload)
        original = payload.get("project") or {}
        restored_id = new_id("prj")
        destination = self.paths.project_dir(restored_id)
        if destination.exists():
            raise ValueError("ARCHIVE_RESTORE_DESTINATION_COLLISION")
        staged_root = Path(
            tempfile.mkdtemp(prefix=f".restore-{restored_id}-", dir=self.paths.projects)
        )
        staged_project = staged_root / "project"
        staged_project.mkdir()
        if files_dir.exists():
            shutil.copytree(files_dir, staged_project, dirs_exist_ok=True)
        for child in ("assets", "datasets", "runs", "notebooks", "scores", "trash"):
            (staged_project / child).mkdir(parents=True, exist_ok=True)
        marker = staged_project / ".restore-incomplete"
        marker.write_text(restored_id, encoding="utf-8")
        old_project_dir = _common_old_project_dir(payload)
        id_map: dict[str, str] = {str(original.get("id")): restored_id}
        for table in RESOURCE_ORDER:
            for row in payload.get(table, []):
                id_map[str(row["id"])] = new_id(PREFIXES[table])
        timestamp = now_iso()
        mode = str(original.get("mode") or "semi_trusted")
        if mode not in {"semi_trusted", "fully_trusted"}:
            mode = "semi_trusted"
        metadata = dict(original.get("metadata") or {})
        metadata.update(
            restored_from_project_id=original.get("id"),
            archive_schema=payload.get("schema_version"),
        )
        project_row = {
            "id": restored_id,
            "name": f"{str(original.get('name') or '恢复项目')[:110]} · 恢复",
            "description": str(original.get("description") or "")[:2000],
            "status": "active",
            "mode": mode,
            "created_at": timestamp,
            "updated_at": timestamp,
            "archived_at": None,
            "trashed_at": None,
            "metadata_json": metadata,
            "legacy_readonly": False,
        }
        project_manifest = staged_project / PROJECT_MARKER_FILE
        project_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "risk-agent-project/v1",
                    "project_id": restored_id,
                    "name": project_row["name"],
                    "status": project_row["status"],
                    "mode": project_row["mode"],
                    "created_at": project_row["created_at"],
                    "updated_at": project_row["updated_at"],
                    "restored_from_project_id": original.get("id"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        rows: list[tuple[str, dict[str, Any]]] = [("projects", project_row)]
        manifest_hash_map: dict[str, str] = {}
        try:
            for table in RESOURCE_ORDER:
                for raw in payload.get(table, []):
                    row = _remap_row(dict(raw), id_map, old_project_dir, destination)
                    row["id"] = id_map[str(raw["id"])]
                    if "project_id" in row:
                        row["project_id"] = restored_id
                    if table == "notebooks":
                        row["kernel_id"] = None
                        row["status"] = "idle"
                    row = _replace_exact_values(row, manifest_hash_map)
                    if table == "run_manifests":
                        manifest_payload = dict(row.get("payload") or {})
                        old_hash = str(row.get("manifest_hash") or "")
                        if manifest_payload.get("run_id") in id_map:
                            manifest_payload["run_id"] = id_map[manifest_payload["run_id"]]
                        for section in ("dataset", "target_task"):
                            section_value = dict(manifest_payload.get(section) or {})
                            if section_value.get("id") in id_map:
                                section_value["id"] = id_map[section_value["id"]]
                            manifest_payload[section] = section_value
                        manifest_payload.pop("manifest_sha256", None)
                        manifest_payload["restored_from_manifest_sha256"] = old_hash
                        new_hash = canonical_hash(manifest_payload)
                        manifest_payload["manifest_sha256"] = new_hash
                        row["payload"] = manifest_payload
                        row["manifest_hash"] = new_hash
                        if old_hash:
                            manifest_hash_map[old_hash] = new_hash
                    rows.append((table, _encode_json_fields(row)))
            _verify_restored_files(rows, destination, staged_project)
            with self.database.transaction() as connection:
                for table, row in rows:
                    self.database._insert_on_connection(connection, table, row)
                staged_project.replace(destination)
            marker = destination / ".restore-incomplete"
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                # Startup recovery removes a marker whose matching DB project
                # committed successfully; never delete committed project files.
                pass
        except (ValueError, KeyError):
            if destination.exists() and (destination / ".restore-incomplete").exists():
                shutil.rmtree(destination)
            raise
        except Exception as exc:
            if destination.exists() and (destination / ".restore-incomplete").exists():
                shutil.rmtree(destination)
            raise ValueError("ARCHIVE_RESOURCE_IMPORT_FAILED") from exc
        finally:
            shutil.rmtree(staged_root, ignore_errors=True)
        return self.catalog.get_project(restored_id)

    def _cleanup_interrupted_restores(self) -> None:
        if not self.paths.projects.exists():
            return
        for candidate in self.paths.projects.iterdir():
            if not candidate.is_dir():
                continue
            marker = candidate / ".restore-incomplete"
            if not marker.is_file():
                continue
            if self.database.get("projects", candidate.name):
                marker.unlink(missing_ok=True)
            else:
                shutil.rmtree(candidate)


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
                "metadata_json": {"schema_version": SCHEMA_VERSION},
                "created_at": now_iso(),
            },
        )

    def verify(self, backup_id: str) -> dict[str, Any]:
        record = self.database.get("backups", backup_id)
        if not record:
            raise KeyError(backup_id)
        path = Path(record["path"])
        valid = path.exists() and sha256_file(path) == record["checksum"]
        return {
            "id": backup_id,
            "valid": valid,
            "size_bytes": path.stat().st_size if path.exists() else 0,
        }

    def restore(self, backup_id: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise ValueError("BACKUP_RESTORE_CONFIRMATION_REQUIRED")
        verification = self.verify(backup_id)
        if not verification["valid"]:
            raise ValueError("BACKUP_CHECKSUM_MISMATCH")
        active = [
            run
            for run in self.database.list_all("runs")
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
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output, 4 * 1024 * 1024)


def _validate_encryption_manifest(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("cipher") != "AES-256-GCM"
        or manifest.get("kdf") != "scrypt-n16384-r8-p1"
        or manifest.get("associated_data") != "risk-model-agent-project-v1"
    ):
        raise ValueError("ARCHIVE_CRYPTO_POLICY_UNSUPPORTED")
    for key in ("plaintext_sha256", "ciphertext_sha256"):
        value = manifest.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("ARCHIVE_HASH_MANIFEST_INVALID")
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError("ARCHIVE_HASH_MANIFEST_INVALID") from exc

    def decoded(name: str, expected_length: int | None = None) -> bytes:
        value = manifest.get(name)
        if not isinstance(value, str):
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        try:
            result = base64.urlsafe_b64decode(value.encode("ascii"))
        except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID") from exc
        if expected_length is not None and len(result) != expected_length:
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        return result

    decoded("payload_nonce", 12)
    decoded("payload_tag", 16)
    for wrap_name in ("password_wrap", "recovery_wrap"):
        wrap = manifest.get(wrap_name)
        if not isinstance(wrap, dict):
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        for key, length in (("salt", 16), ("nonce", 12)):
            value = wrap.get(key)
            if not isinstance(value, str):
                raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
            try:
                result = base64.urlsafe_b64decode(value.encode("ascii"))
            except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
                raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID") from exc
            if len(result) != length:
                raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        wrapped = wrap.get("wrapped_key")
        if not isinstance(wrapped, str):
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        try:
            if len(base64.urlsafe_b64decode(wrapped.encode("ascii"))) != 48:
                raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID")
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise ValueError("ARCHIVE_CRYPTO_MANIFEST_INVALID") from exc


def _validate_inner_archive(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("ARCHIVE_MEMBER_LIMIT_EXCEEDED")
    names = [member.filename for member in members]
    if len(names) != len(set(names)) or "project.json" not in names:
        raise ValueError("ARCHIVE_MEMBER_SET_INVALID")
    total = 0
    for member in members:
        if member.flag_bits & 0x1:
            raise ValueError("ARCHIVE_ENCRYPTED_MEMBER_FORBIDDEN")
        mode = (member.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            raise ValueError("ARCHIVE_SYMLINK_FORBIDDEN")
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts or "\x00" in member.filename:
            raise ValueError("ARCHIVE_PATH_TRAVERSAL")
        if member.filename != "project.json" and not member.filename.startswith("files/"):
            raise ValueError("ARCHIVE_MEMBER_SET_INVALID")
        total += member.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError("ARCHIVE_UNPACKED_SIZE_LIMIT_EXCEEDED")
        if member.file_size and member.compress_size == 0:
            raise ValueError("ARCHIVE_COMPRESSION_RATIO_INVALID")
        if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
            raise ValueError("ARCHIVE_COMPRESSION_RATIO_INVALID")


def _validate_project_payload(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != ARCHIVE_SCHEMA:
        raise ValueError("ARCHIVE_PROJECT_SCHEMA_UNSUPPORTED")
    project = payload.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("id"), str):
        raise ValueError("ARCHIVE_PROJECT_RECORD_INVALID")
    unknown = set(payload) - {"schema_version", "project", *RESOURCE_ORDER}
    if unknown:
        raise ValueError(f"ARCHIVE_PROJECT_KEYS_UNSUPPORTED: {sorted(unknown)}")
    seen = {project["id"]}
    count = 0
    for table in RESOURCE_ORDER:
        rows = payload.get(table, [])
        if not isinstance(rows, list):
            raise ValueError(f"ARCHIVE_RESOURCE_LIST_INVALID: {table}")
        for row in rows:
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise ValueError("ARCHIVE_RESOURCE_LIMIT_EXCEEDED")
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError(f"ARCHIVE_RESOURCE_RECORD_INVALID: {table}")
            if row["id"] in seen:
                raise ValueError("ARCHIVE_RESOURCE_ID_DUPLICATE")
            seen.add(row["id"])


def _common_old_project_dir(payload: dict[str, Any]) -> Path | None:
    paths: list[Path] = []
    for table in RESOURCE_ORDER:
        for row in payload.get(table, []):
            for key in PATH_FIELDS:
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
    return {key: _remap_value(value, key, id_map, old_root, new_root) for key, value in row.items()}


def _remap_value(
    value: Any,
    key: str,
    id_map: dict[str, str],
    old_root: Path | None,
    new_root: Path,
) -> Any:
    if value is None:
        return None
    if key in PATH_FIELDS:
        if not isinstance(value, str) or not value:
            raise ValueError(f"ARCHIVE_PATH_INVALID: {key}")
        if old_root is None:
            raise ValueError("ARCHIVE_OLD_PROJECT_ROOT_UNRESOLVED")
        original = Path(value)
        if not original.is_absolute():
            raise ValueError("ARCHIVE_RELATIVE_STORED_PATH_FORBIDDEN")
        try:
            relative = original.relative_to(old_root)
        except ValueError as exc:
            raise ValueError(f"ARCHIVE_PATH_OUTSIDE_PROJECT: {key}") from exc
        mapped = (new_root / relative).resolve()
        root = new_root.resolve()
        if mapped != root and root not in mapped.parents:
            raise ValueError("ARCHIVE_PATH_OUTSIDE_PROJECT")
        return str(mapped)
    if key.endswith("_id") and isinstance(value, str):
        if key == "kernel_id":
            return None
        if value in id_map:
            return id_map[value]
        if key in REQUIRED_REFERENCE_FIELDS:
            raise ValueError(f"ARCHIVE_REFERENCE_UNRESOLVED: {key}")
        return value
    if key.endswith("_ids") and isinstance(value, list):
        mapped: list[Any] = []
        for item in value:
            if isinstance(item, str) and item in id_map:
                mapped.append(id_map[item])
                continue
            if key in REQUIRED_REFERENCE_LIST_FIELDS:
                raise ValueError(f"ARCHIVE_REFERENCE_UNRESOLVED: {key}")
            mapped.append(item)
        return mapped
    if isinstance(value, dict):
        return {
            str(child_key): _remap_value(child_value, str(child_key), id_map, old_root, new_root)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _remap_value(item, key, id_map, old_root, new_root)
            if isinstance(item, (dict, list))
            else item
            for item in value
        ]
    return value


def _verify_restored_files(
    rows: list[tuple[str, dict[str, Any]]], final_root: Path, staged_root: Path
) -> None:
    final_root = final_root.resolve()
    staged_root = staged_root.resolve()
    for table, row in rows:
        for key, value in _iter_path_fields(row):
            final = Path(str(value)).resolve()
            try:
                relative = final.relative_to(final_root)
            except ValueError as exc:
                raise ValueError("ARCHIVE_RESTORED_PATH_OUTSIDE_PROJECT") from exc
            staged = (staged_root / relative).resolve()
            if staged_root not in staged.parents or not staged.is_file():
                raise ValueError(f"ARCHIVE_REFERENCED_FILE_MISSING: {table}.{key}")
            expected_hash = None
            top_level = row.get(key) == value
            if top_level and table == "data_assets" and key == "stored_path":
                expected_hash = row.get("sha256")
                if int(row.get("size_bytes") or -1) != staged.stat().st_size:
                    raise ValueError("ARCHIVE_ASSET_SIZE_MISMATCH")
            elif top_level and table == "artifacts" and key == "path":
                expected_hash = row.get("checksum")
            elif top_level and table == "model_versions" and key == "artifact_path":
                expected_hash = row.get("checksum")
            if expected_hash and sha256_file(staged) != expected_hash:
                raise ValueError(f"ARCHIVE_REFERENCED_FILE_CHECKSUM_MISMATCH: {table}.{key}")


def _iter_path_fields(value: Any) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PATH_FIELDS and child:
                yield key, child
            else:
                yield from _iter_path_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_path_fields(child)


def _encode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in list(result):
        json_key = f"{key}_json"
        if json_key in JSON_COLUMNS and key not in {"id"}:
            result[json_key] = result.pop(key)
    return result


def _replace_exact_values(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_exact_values(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_exact_values(child, mapping) for child in value]
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return value
