from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from app.core.database import Database, new_id, now_iso
from app.core.paths import AppPaths
from app.core.security import sha256_file


def test_schema_upgrade_creates_verified_backup_before_ddl(tmp_path: Path):
    paths = AppPaths(tmp_path / "RiskModelAgent").ensure()
    with sqlite3.connect(paths.database) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES ('version', '1')")
    database = Database(paths=paths)
    with database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()[0]
    assert version == "2"
    backups = database.list("backups", {"kind": "schema_upgrade"}, limit=10)
    assert len(backups) == 1
    backup_path = Path(backups[0]["path"])
    assert backup_path.is_file()
    assert sha256_file(backup_path) == backups[0]["checksum"]
    with sqlite3.connect(backup_path) as connection:
        assert (
            connection.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]
            == "1"
        )


def test_pre_trace_incomplete_run_is_preserved_and_requires_restart(context):
    project = context.catalog.create_project("升级前运行")
    identifier = new_id("run")
    timestamp = now_iso()
    context.database.insert(
        "runs",
        {
            "id": identifier,
            "project_id": project["id"],
            "target_task_id": None,
            "status": "running",
            "stage": "training",
            "node": "train_review",
            "mode": "semi_trusted",
            "seq": 0,
            "progress": 0.5,
            "state_json": {"legacy": True},
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    assert context.engine.recover_incomplete() == []
    run = context.catalog.require("runs", identifier)
    assert run["status"] == "blocked"
    assert run["error"] == "RUN_RESTART_REQUIRED_AFTER_TRACE_SCHEMA_UPGRADE"
    assert run["state"] == {"legacy": True}
    event = context.database.list("events", {"run_id": identifier}, limit=1)[0]
    assert event["evidence"]["legacy_state_preserved"] is True


def test_legacy_upgrade_backs_up_copies_and_keeps_old_runs_readonly(
    context, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("app.providers.secrets._keyring", lambda: None)
    legacy = tmp_path / "runtime"
    data_dir = legacy / "projects" / "old_project" / "datasets"
    data_dir.mkdir(parents=True)
    source = data_dir / "legacy.csv"
    source.write_text("Y,x\n0,1\n1,2\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    database = legacy / "risk_agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
              id TEXT PRIMARY KEY, name TEXT, status TEXT, created_at TEXT
            );
            CREATE TABLE datasets (
              id TEXT PRIMARY KEY, project_id TEXT, path TEXT, filename TEXT,
              sha256 TEXT, bytes INTEGER, rows INTEGER, columns INTEGER,
              sheet TEXT, is_demo INTEGER, profile_json TEXT, created_at TEXT
            );
            CREATE TABLE runs (
              id TEXT PRIMARY KEY, project_id TEXT, dataset_id TEXT, status TEXT,
              phase TEXT, mode TEXT, state_json TEXT, error TEXT, created_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?)",
            ("old_project", "旧项目", "active", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old_dataset",
                "old_project",
                str(source),
                source.name,
                digest,
                source.stat().st_size,
                2,
                2,
                None,
                0,
                json.dumps({"rows": 2, "columns": 2}),
                "2026-01-01T00:01:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old_run",
                "old_project",
                "old_dataset",
                "completed",
                "report",
                "assisted",
                json.dumps({"legacy": True}),
                None,
                "2026-01-01T00:02:00+00:00",
            ),
        )
    (legacy / "app-config.json").write_text(
        json.dumps({"provider": "kimi", "telemetry": False}), encoding="utf-8"
    )
    (legacy / "secrets").mkdir()
    (legacy / "secrets" / "provider_api_key").write_text(
        "legacy-secret-for-test", encoding="utf-8"
    )

    result = context.migration.migrate(legacy)
    assert result["status"] == "completed"
    assert result["projects"] == 1
    assert result["datasets"] == 1
    assert result["legacy_runs"] == 1
    assert result["source_deleted"] is False
    assert database.exists()
    assert Path(result["backup"]).exists()
    backups = context.database.list("backups", {"kind": "pre_upgrade_v0"}, limit=10)
    assert len(backups) == 1
    assert backups[0]["checksum"] == hashlib.sha256(Path(result["backup"]).read_bytes()).hexdigest()
    assert Path(result["copied_root"]).joinpath("risk_agent.sqlite3").exists()
    old_run = context.database.list(
        "legacy_records", {"record_type": "run", "source_id": "old_run"}, limit=1
    )[0]
    assert old_run["metadata"]["state"] == {"legacy": True}
    assert context.migration.migrate(legacy) == result
