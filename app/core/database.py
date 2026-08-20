from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import AppPaths, get_paths


SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


JSON_COLUMNS = {
    "metadata_json",
    "parent_ids_json",
    "lineage_json",
    "profile_json",
    "steps_json",
    "validation_json",
    "labels_json",
    "split_json",
    "screening_json",
    "binning_json",
    "model_plan_json",
    "state_json",
    "issues_json",
    "evidence_json",
    "metrics_json",
    "contract_json",
    "payload_json",
    "review_json",
    "usage_json",
}

TABLES = {
    "projects",
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
    "provider_requests",
    "legacy_records",
    "archives",
    "backups",
}


DDL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active', mode TEXT NOT NULL DEFAULT 'semi_trusted',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT, trashed_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}', legacy_readonly INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS data_assets (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
  kind TEXT NOT NULL, format TEXT NOT NULL, stored_path TEXT NOT NULL, sheet TEXT,
  sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, rows INTEGER, columns INTEGER,
  status TEXT NOT NULL DEFAULT 'ready', metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_versions (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), label TEXT NOT NULL,
  stored_path TEXT NOT NULL, format TEXT NOT NULL, sheet TEXT, rows INTEGER NOT NULL,
  columns INTEGER NOT NULL, parent_ids_json TEXT NOT NULL DEFAULT '[]',
  lineage_json TEXT NOT NULL DEFAULT '{}', profile_json TEXT NOT NULL DEFAULT '{}',
  is_frozen INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS join_plans (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft', base_asset_id TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]', validation_json TEXT NOT NULL DEFAULT '{}',
  output_dataset_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS target_tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  dataset_version_id TEXT NOT NULL REFERENCES dataset_versions(id), target_column TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', labels_json TEXT NOT NULL DEFAULT '{}',
  valid_sample_count INTEGER NOT NULL DEFAULT 0, split_json TEXT NOT NULL DEFAULT '{}',
  screening_json TEXT NOT NULL DEFAULT '{}', binning_json TEXT NOT NULL DEFAULT '{}',
  model_plan_json TEXT NOT NULL DEFAULT '{}', queue_position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  target_task_id TEXT REFERENCES target_tasks(id), status TEXT NOT NULL DEFAULT 'queued',
  stage TEXT NOT NULL DEFAULT 'project_setup', node TEXT NOT NULL DEFAULT 'start',
  mode TEXT NOT NULL DEFAULT 'semi_trusted', seq INTEGER NOT NULL DEFAULT 0,
  progress REAL NOT NULL DEFAULT 0, state_json TEXT NOT NULL DEFAULT '{}', error TEXT,
  started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), node TEXT NOT NULL,
  seq INTEGER NOT NULL, state_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(run_id, seq)
);
CREATE TABLE IF NOT EXISTS review_records (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), round INTEGER NOT NULL,
  scope TEXT NOT NULL, status TEXT NOT NULL, issues_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_versions (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), target_task_id TEXT NOT NULL,
  name TEXT NOT NULL, algorithm TEXT NOT NULL, status TEXT NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}', artifact_path TEXT, contract_json TEXT NOT NULL DEFAULT '{}',
  checksum TEXT, champion INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), kind TEXT NOT NULL,
  name TEXT NOT NULL, path TEXT NOT NULL, mime_type TEXT NOT NULL, checksum TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), run_id TEXT,
  title TEXT NOT NULL DEFAULT '项目 Agent 对话', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL, agent TEXT, content TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_events (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  seq INTEGER NOT NULL, status TEXT NOT NULL, agent TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(conversation_id, seq)
);
CREATE TABLE IF NOT EXISTS message_feedback (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES conversation_messages(id),
  rating TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), stage TEXT NOT NULL,
  kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', payload_json TEXT NOT NULL DEFAULT '{}',
  review_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
  stage TEXT NOT NULL, node TEXT NOT NULL, agent TEXT NOT NULL, tool TEXT,
  status TEXT NOT NULL, summary TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, UNIQUE(run_id, seq)
);
CREATE TABLE IF NOT EXISTS notebooks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), dataset_version_id TEXT,
  name TEXT NOT NULL, path TEXT NOT NULL, kernel_id TEXT, status TEXT NOT NULL DEFAULT 'idle',
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS score_jobs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
  model_version_id TEXT NOT NULL REFERENCES model_versions(id), input_asset_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', output_path TEXT, rows INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_requests (
  id TEXT PRIMARY KEY, run_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL,
  status TEXT NOT NULL, safe_payload_hash TEXT NOT NULL, usage_json TEXT NOT NULL DEFAULT '{}',
  response_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_records (
  id TEXT PRIMARY KEY, record_type TEXT NOT NULL, source_id TEXT NOT NULL,
  source_path TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(record_type, source_id)
);
CREATE TABLE IF NOT EXISTS archives (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
  path TEXT NOT NULL, checksum TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready', metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, path TEXT NOT NULL,
  checksum TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_project ON data_assets(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_datasets_project ON dataset_versions(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_targets_project ON target_tasks(project_id, queue_position);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id, kind);
CREATE INDEX IF NOT EXISTS idx_archives_project ON archives(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_events ON conversation_events(conversation_id, seq);
"""


class Database:
    def __init__(self, path: Path | None = None, paths: AppPaths | None = None):
        self.paths = paths or get_paths()
        self.path = (path or self.paths.database).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(DDL)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _table(name: str) -> str:
        if name not in TABLES:
            raise ValueError(f"UNKNOWN_TABLE: {name}")
        return name

    @staticmethod
    def _encode(data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in JSON_COLUMNS and not isinstance(value, str):
                result[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            elif isinstance(value, bool):
                result[key] = int(value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in JSON_COLUMNS.intersection(result):
            try:
                result[key.removesuffix("_json")] = json.loads(result.pop(key) or "null")
            except json.JSONDecodeError:
                result[key.removesuffix("_json")] = None
        return result

    def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table)
        encoded = self._encode(data)
        columns = ", ".join(encoded)
        placeholders = ", ".join("?" for _ in encoded)
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(encoded.values())
            )
        result = self.get(table, str(data["id"]))
        if result is None:  # pragma: no cover
            raise RuntimeError("INSERT_NOT_VISIBLE")
        return result

    def get(self, table: str, identifier: str) -> dict[str, Any] | None:
        table = self._table(table)
        with self.connect() as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        return self._decode(row)

    def list(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        order_by: str = "created_at DESC",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        table = self._table(table)
        filters = filters or {}
        if not re_safe_order(order_by):
            raise ValueError("UNSAFE_ORDER_BY")
        where = " AND ".join(f"{key}=?" for key in filters) or "1=1"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by} LIMIT ?",
                (*filters.values(), min(max(limit, 1), 5000)),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def update(self, table: str, identifier: str, data: dict[str, Any]) -> dict[str, Any]:
        table = self._table(table)
        encoded = self._encode(data)
        if not encoded:
            current = self.get(table, identifier)
            if current is None:
                raise KeyError(identifier)
            return current
        assignments = ", ".join(f"{key}=?" for key in encoded)
        with self.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id=?", (*encoded.values(), identifier)
            )
            if cursor.rowcount != 1:
                raise KeyError(identifier)
        result = self.get(table, identifier)
        if result is None:  # pragma: no cover
            raise KeyError(identifier)
        return result

    def delete(self, table: str, identifier: str) -> None:
        table = self._table(table)
        with self.transaction() as connection:
            connection.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))

    def append_event(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM events WHERE run_id=?", (run_id,)
            ).fetchone()
            seq = int(row["next_seq"])
            event = {
                "id": new_id("evt"),
                "run_id": run_id,
                "seq": seq,
                "stage": payload["stage"],
                "node": payload["node"],
                "agent": payload.get("agent", "local_worker"),
                "tool": payload.get("tool"),
                "status": payload["status"],
                "summary": payload["summary"],
                "evidence_json": payload.get("evidence", {}),
                "created_at": payload.get("created_at", now_iso()),
            }
            encoded = self._encode(event)
            columns = ", ".join(encoded)
            placeholders = ", ".join("?" for _ in encoded)
            connection.execute(
                f"INSERT INTO events ({columns}) VALUES ({placeholders})", tuple(encoded.values())
            )
            connection.execute(
                "UPDATE runs SET seq=?, stage=?, node=?, updated_at=? WHERE id=?",
                (seq, event["stage"], event["node"], now_iso(), run_id),
            )
        result = self.get("events", event["id"])
        return result or event

    def backup(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def restore_from_backup(self, source: Path) -> Path:
        """Atomically replace the active database without replaying stale WAL pages."""
        source = source.resolve()
        staged = self.path.with_name(
            f".{self.path.name}.restore-{secrets.token_hex(6)}"
        )
        with self._lock:
            try:
                shutil.copy2(source, staged)
                with sqlite3.connect(staged) as candidate:
                    integrity = candidate.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError("BACKUP_INTEGRITY_CHECK_FAILED")
                with self.connect() as active:
                    active.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    Path(f"{self.path}{suffix}").unlink(missing_ok=True)
                staged.replace(self.path)
                self.initialize()
            finally:
                staged.unlink(missing_ok=True)
        return self.path


def re_safe_order(value: str) -> bool:
    allowed = {
        "created_at DESC",
        "created_at ASC",
        "updated_at DESC",
        "queue_position ASC",
        "seq ASC",
        "round ASC",
    }
    return value in allowed


def snapshot_legacy_database(legacy_database: Path, paths: AppPaths | None = None) -> Path | None:
    if not legacy_database.exists():
        return None
    target_paths = paths or get_paths()
    target = target_paths.backups / f"legacy-v0-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
    if not target.exists():
        shutil.copy2(legacy_database, target)
    return target
