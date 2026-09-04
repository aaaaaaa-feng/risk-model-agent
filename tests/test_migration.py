from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.api.runs import get_run
from app.core.database import Database, new_id, now_iso
from app.core.config import SettingsStore
from app.core.paths import AppPaths
from app.core.security import sha256_file
from app.governance.manifest import MANIFEST_SCHEMA, build_run_manifest, canonical_hash
from app.workers.demo import install_demo_project


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


def _insert_incomplete_previous_graph_run(
    context,
    *,
    tamper_manifest: bool = False,
    non_object_manifest: bool = False,
    non_object_decision: bool = False,
    non_object_trace_evidence: bool = False,
):
    demo = install_demo_project(context.catalog, mode="semi_trusted", rows=500)
    task = demo["target_tasks"][0]
    dataset = context.catalog.require("dataset_versions", task["dataset_version_id"])
    identifier = new_id("run")
    timestamp = now_iso()
    manifest = build_run_manifest(
        run_id=identifier,
        target_task=task,
        dataset=dataset,
        registry=context.pipeline.registry,
        settings=SettingsStore(context.paths).load(),
        started_at=timestamp,
    )
    manifest["agent_graph_version"] = "risk-model-agent-graph/v1"
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_hash(manifest)
    if tamper_manifest:
        manifest["agent_graph_version"] = "risk-model-agent-graph/v2"
    trace, root_span = context.engine.traces.new_run_rows(
        identifier,
        started_at=timestamp,
        manifest_hash=manifest["manifest_sha256"],
    )
    trace["status"] = "running"
    root_span["status"] = "running"
    if non_object_trace_evidence:
        root_span["evidence_json"] = []
    child_span = {
        **root_span,
        "id": new_id("span"),
        "parent_span_id": root_span["id"],
        "kind": "tool",
        "stage": "code_review",
        "node": "code_review",
        "agent": "code_agent",
        "tool": "legacy_codegen",
        "summary": "旧版运行中的工具节点",
    }
    state = {
        "run_id": identifier,
        "project_id": demo["project"]["id"],
        "target_task_id": task["id"],
        "legacy": True,
    }
    context.database.insert_many_atomic(
        [
            (
                "runs",
                {
                    "id": identifier,
                    "project_id": demo["project"]["id"],
                    "target_task_id": task["id"],
                    "status": "running",
                    "stage": "code_review",
                    "node": "code_review",
                    "mode": "semi_trusted",
                    "seq": 0,
                    "progress": 0.7,
                    "state_json": state,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            ),
            (
                "run_manifests",
                {
                    "id": new_id("manifest"),
                    "run_id": identifier,
                    "schema_version": MANIFEST_SCHEMA,
                    "manifest_hash": manifest["manifest_sha256"],
                    "payload_json": [] if non_object_manifest else manifest,
                    "created_at": timestamp,
                },
            ),
            ("traces", trace),
            ("trace_spans", root_span),
            ("trace_spans", child_span),
            (
                "decisions",
                {
                    "id": new_id("decision"),
                    "run_id": identifier,
                    "stage": "code_review",
                    "kind": "confirm_code",
                    "status": "pending",
                    "payload_json": (
                        []
                        if non_object_decision
                        else {"title": "旧版待确认", "draft": {"keep": True}}
                    ),
                    "review_json": {},
                    "created_at": timestamp,
                },
            ),
            (
                "decisions",
                {
                    "id": new_id("decision"),
                    "run_id": identifier,
                    "stage": "code_review",
                    "kind": "confirm_code",
                    "status": "submitted",
                    "payload_json": {
                        "title": "旧版已提交确认",
                        "response": {"approved": True, "edits": {"keep": "yes"}},
                    },
                    "review_json": {},
                    "created_at": timestamp,
                },
            ),
        ]
    )
    return identifier, task, state


def test_incomplete_run_from_previous_graph_is_preserved_and_blocked(context):
    identifier, _task, state = _insert_incomplete_previous_graph_run(context)

    assert context.engine.recover_incomplete() == []
    run = context.catalog.require("runs", identifier)
    assert run["status"] == "blocked"
    assert run["error"] == "RUN_RESTART_REQUIRED_AFTER_GRAPH_UPGRADE"
    assert run["state"] == state
    event = context.database.list("events", {"run_id": identifier}, limit=1)[0]
    assert event["evidence"]["previous_graph_version"] == "risk-model-agent-graph/v1"
    assert event["evidence"]["legacy_state_preserved"] is True
    decisions = context.database.list("decisions", {"run_id": identifier}, limit=10)
    assert len(decisions) == 2
    assert {item["status"] for item in decisions} == {"cancelled"}
    assert all(item["resolved_at"] for item in decisions)
    assert all(
        item["payload"]["upgrade_resolution"]["reason_code"]
        == "RUN_RESTART_REQUIRED_AFTER_GRAPH_UPGRADE"
        for item in decisions
    )
    submitted = next(item for item in decisions if "response" in item["payload"])
    assert submitted["payload"]["response"]["edits"] == {"keep": "yes"}
    trace = context.database.list(
        "traces", {"run_id": identifier}, order_by="started_at DESC", limit=1
    )[0]
    assert trace["status"] == "blocked"
    assert trace["finished_at"]
    spans = context.database.list(
        "trace_spans", {"trace_id": trace["id"]}, order_by="started_at ASC", limit=10
    )
    assert len(spans) == 2
    assert {item["status"] for item in spans} == {"blocked"}
    assert all(item["finished_at"] and item["duration_ms"] is not None for item in spans)
    assert all(
        item["evidence"]["terminal_transition"]["reason_code"]
        == "RUN_RESTART_REQUIRED_AFTER_GRAPH_UPGRADE"
        for item in spans
    )
    assert get_run(identifier, ctx=context)["pending_decisions"] == []

    context.database.insert(
        "decisions",
        {
            "id": new_id("decision"),
            "run_id": identifier,
            "stage": "code_review",
            "kind": "legacy_stale_decision",
            "status": "pending",
            "payload_json": {"title": "损坏数据中的遗留确认"},
            "review_json": {},
            "created_at": now_iso(),
        },
    )
    assert get_run(identifier, ctx=context)["pending_decisions"] == []


def test_tampered_manifest_cannot_resume_by_spoofing_current_graph_version(context):
    identifier, _task, state = _insert_incomplete_previous_graph_run(context, tamper_manifest=True)

    assert context.engine.recover_incomplete() == []
    run = context.catalog.require("runs", identifier)
    assert run["status"] == "blocked"
    assert run["state"] == state
    assert run["error"] == "RUN_RESTART_REQUIRED_AFTER_MANIFEST_VALIDATION_FAILURE"
    event = context.database.list("events", {"run_id": identifier}, limit=1)[0]
    assert event["evidence"]["manifest_validation_error"] == "RUN_MANIFEST_INTEGRITY_FAILED"


def test_non_object_manifest_blocks_only_the_affected_run(context):
    identifier, _task, state = _insert_incomplete_previous_graph_run(
        context, non_object_manifest=True
    )

    assert context.engine.recover_incomplete() == []
    run = context.catalog.require("runs", identifier)
    assert run["status"] == "blocked"
    assert run["state"] == state
    assert run["error"] == "RUN_RESTART_REQUIRED_AFTER_MANIFEST_VALIDATION_FAILURE"
    event = context.database.list("events", {"run_id": identifier}, limit=1)[0]
    assert event["evidence"]["manifest_validation_error"] == "RUN_MANIFEST_PAYLOAD_INVALID"


def test_non_object_decision_does_not_block_upgrade_recovery(context):
    malformed_run, _task, _state = _insert_incomplete_previous_graph_run(
        context,
        non_object_decision=True,
        non_object_trace_evidence=True,
    )
    healthy_run, _task, _state = _insert_incomplete_previous_graph_run(context)
    with context.database.connect() as connection:
        malformed_decision = connection.execute(
            "SELECT id, payload_json FROM decisions WHERE run_id=? AND payload_json='[]'",
            (malformed_run,),
        ).fetchone()
    assert malformed_decision is not None

    assert context.engine.recover_incomplete() == []
    assert context.catalog.require("runs", malformed_run)["status"] == "blocked"
    assert context.catalog.require("runs", healthy_run)["status"] == "blocked"
    with context.database.connect() as connection:
        preserved = connection.execute(
            "SELECT status, payload_json, resolved_at FROM decisions WHERE id=?",
            (malformed_decision["id"],),
        ).fetchone()
    assert preserved["status"] == "cancelled"
    assert preserved["payload_json"] == "[]"
    assert preserved["resolved_at"]
    with context.database.connect() as connection:
        preserved_span = connection.execute(
            "SELECT status, evidence_json, finished_at FROM trace_spans "
            "WHERE run_id=? AND parent_span_id IS NULL",
            (malformed_run,),
        ).fetchone()
    assert preserved_span["status"] == "blocked"
    assert preserved_span["evidence_json"] == "[]"
    assert preserved_span["finished_at"]


def test_graph_upgrade_block_rolls_back_run_task_and_event_together(context, monkeypatch):
    identifier, task, _state = _insert_incomplete_previous_graph_run(context)
    initial_task_status = context.catalog.require("target_tasks", task["id"])["status"]
    original_insert = context.database._insert_on_connection

    def fail_event_insert(connection, table, data):
        if table == "events":
            raise RuntimeError("injected event failure")
        return original_insert(connection, table, data)

    monkeypatch.setattr(context.database, "_insert_on_connection", fail_event_insert)

    with pytest.raises(RuntimeError, match="injected event failure"):
        context.engine.recover_incomplete()

    run = context.catalog.require("runs", identifier)
    assert run["status"] == "running"
    assert run["error"] is None
    assert context.catalog.require("target_tasks", task["id"])["status"] == initial_task_status
    assert context.database.list("events", {"run_id": identifier}, limit=10) == []
    decisions = context.database.list("decisions", {"run_id": identifier}, limit=10)
    assert {item["status"] for item in decisions} == {"pending", "submitted"}
    assert all(item["resolved_at"] is None for item in decisions)
    trace = context.database.list(
        "traces", {"run_id": identifier}, order_by="started_at DESC", limit=1
    )[0]
    assert trace["status"] == "running"
    assert trace["finished_at"] is None
    spans = context.database.list(
        "trace_spans", {"trace_id": trace["id"]}, order_by="started_at ASC", limit=10
    )
    assert {item["status"] for item in spans} == {"running"}
    assert all(item["finished_at"] is None for item in spans)


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
    (legacy / "secrets" / "provider_api_key").write_text("legacy-secret-for-test", encoding="utf-8")

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
