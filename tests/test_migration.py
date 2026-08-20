from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


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
