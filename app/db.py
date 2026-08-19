"""Small SQLite repository for projects, immutable runs, and audit events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

PROJECT_FIELDS = {
    "name",
    "status",
    "dataset_filename",
    "dataset_sha256",
    "dataset_size",
    "dataset_rows",
    "dataset_columns",
    "dataset_is_demo",
    "profile_path",
    "plan_path",
    "plan_version",
    "approved_plan_hash",
    "approved_at",
    "latest_run_id",
}

RUN_FIELDS = {
    "status",
    "result_path",
    "model_path",
    "report_path",
    "error_message",
    "completed_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dataset_filename TEXT,
                    dataset_sha256 TEXT,
                    dataset_size INTEGER,
                    dataset_rows INTEGER,
                    dataset_columns INTEGER,
                    dataset_is_demo INTEGER NOT NULL DEFAULT 0,
                    profile_path TEXT,
                    plan_path TEXT,
                    plan_version INTEGER NOT NULL DEFAULT 0,
                    approved_plan_hash TEXT,
                    approved_at TEXT,
                    latest_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    result_path TEXT,
                    model_path TEXT,
                    report_path TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, id DESC);
                """
            )

    def create_project(
        self, project_id: str, name: str, status: str = "profiled"
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, name, status, now, now),
            )
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(row)

    def list_projects(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, **fields: Any) -> Dict[str, Any]:
        unknown = set(fields) - PROJECT_FIELDS
        if unknown:
            raise ValueError("Unsupported project fields: " + ", ".join(sorted(unknown)))
        if not fields:
            return self.get_project(project_id)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [project_id]
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE projects SET {assignments} WHERE id = ?",  # nosec: fields are allowlisted
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(project_id)
        return self.get_project(project_id)

    def create_run(self, run_id: str, project_id: str, plan_hash: str) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, project_id, status, plan_hash, created_at)
                VALUES (?, ?, 'training', ?, ?)
                """,
                (run_id, project_id, plan_hash, utc_now()),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def list_runs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run(self, run_id: str, **fields: Any) -> Dict[str, Any]:
        unknown = set(fields) - RUN_FIELDS
        if unknown:
            raise ValueError("Unsupported run fields: " + ", ".join(sorted(unknown)))
        if not fields:
            return self.get_run(run_id)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [run_id]
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {assignments} WHERE id = ?",  # nosec: fields are allowlisted
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)
        return self.get_run(run_id)

    def add_event(
        self,
        project_id: str,
        event_type: str,
        from_status: Optional[str],
        to_status: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_payload = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO events
                    (project_id, event_type, from_status, to_status, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, event_type, from_status, to_status, safe_payload, utc_now()),
            )

    def list_events(self, project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events WHERE project_id = ? ORDER BY id DESC LIMIT ?
                """,
                (project_id, max(1, min(limit, 500))),
            ).fetchall()
        events: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            events.append(item)
        return events
