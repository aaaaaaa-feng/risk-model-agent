from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.database import new_id, now_iso
from app.services.archives import _remap_row
from app.workers.demo import install_demo_project

from conftest import wait_for_run


def test_encrypted_project_archive_password_and_recovery(context):
    project = context.catalog.create_project("长期归档项目")
    record, recovery = context.archives.create(project["id"], "strong archive password")
    assert Path(record["path"]).exists()
    manifest = context.archives.inspect(Path(record["path"]))
    assert manifest["cipher"] == "AES-256-GCM"
    restored_by_password = context.archives.restore(Path(record["path"]), "strong archive password")
    restored_by_recovery = context.archives.restore(Path(record["path"]), recovery)
    assert restored_by_password["name"].endswith("· 恢复")
    assert restored_by_recovery["name"].endswith("· 恢复")
    with pytest.raises(ValueError, match="INVALID_ARCHIVE_OR_CREDENTIAL"):
        context.archives.restore(Path(record["path"]), "incorrect password")


def test_current_project_archive_restore_does_not_recreate_notebook_directory(context):
    project = context.catalog.create_project("当前项目归档")
    record, _ = context.archives.create(project["id"], "current-project-password")

    restored = context.archives.restore(Path(record["path"]), "current-project-password")

    assert not (context.paths.project_dir(restored["id"]) / "notebooks").exists()


def test_archive_restore_is_transactional_and_paths_fail_closed(context, monkeypatch):
    project = context.catalog.create_project("原子恢复项目")
    context.catalog.create_dataset_version(
        project["id"],
        pd.DataFrame({"order_id": ["O1", "O2"], "Y": [0, 1]}),
        "基线",
        [],
        {"kind": "test"},
    )
    archive, _ = context.archives.create(project["id"], "transaction-safe-password")
    before_ids = {item["id"] for item in context.database.list_all("projects")}
    original = context.database._insert_on_connection

    def fail_dataset(connection, table, data):
        if table == "dataset_versions":
            raise RuntimeError("injected restore failure")
        return original(connection, table, data)

    monkeypatch.setattr(context.database, "_insert_on_connection", fail_dataset)
    with pytest.raises(ValueError, match="ARCHIVE_RESOURCE_IMPORT_FAILED"):
        context.archives.restore(Path(archive["path"]), "transaction-safe-password")
    assert {item["id"] for item in context.database.list_all("projects")} == before_ids
    assert not list(context.paths.projects.glob(".restore-*"))
    assert not any(
        (path / ".restore-incomplete").exists() for path in context.paths.projects.iterdir()
    )

    with pytest.raises(ValueError, match="ARCHIVE_PATH_OUTSIDE_PROJECT"):
        _remap_row(
            {"stored_path": "/outside/project/data.csv"},
            {},
            Path("/old/project"),
            Path("/new/project"),
        )


def test_archive_creation_blocks_active_runs(context):
    project = context.catalog.create_project("运行中不可归档")
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
            "state_json": {},
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    with pytest.raises(ValueError, match="ARCHIVE_CREATE_BLOCKED_BY_ACTIVE_RUN"):
        context.archives.create(project["id"], "active-run-password")


def test_archive_restores_manifest_and_trace_with_rebound_hashes(context):
    demo = install_demo_project(context.catalog, mode="semi_trusted", rows=500)
    created = context.engine.create_run(
        demo["project"]["id"], demo["target_tasks"][0]["id"], "semi_trusted"
    )
    run = wait_for_run(context, created["id"], {"awaiting_decision", "failed"}, 30)
    assert run["status"] == "awaiting_decision"
    decision = next(
        item
        for item in context.database.list_all("decisions", {"run_id": run["id"]})
        if item["status"] == "pending"
    )
    context.engine.resume(run["id"], decision["id"], False, {})
    assert wait_for_run(context, run["id"], {"blocked", "failed"}, 30)["status"] == "blocked"
    original_manifest = context.database.list("run_manifests", {"run_id": run["id"]}, limit=1)[0]
    archive, _ = context.archives.create(demo["project"]["id"], "trace-archive-password")
    restored = context.archives.restore(Path(archive["path"]), "trace-archive-password")
    restored_run = context.database.list("runs", {"project_id": restored["id"]}, limit=1)[0]
    restored_manifest = context.database.list(
        "run_manifests", {"run_id": restored_run["id"]}, limit=1
    )[0]
    restored_trace = context.database.list(
        "traces", {"run_id": restored_run["id"]}, order_by="started_at ASC", limit=1
    )[0]
    spans = context.database.list_all(
        "trace_spans", {"trace_id": restored_trace["id"]}, order_by="started_at ASC"
    )
    assert restored_manifest["manifest_hash"] == restored_manifest["payload"]["manifest_sha256"]
    assert restored_manifest["manifest_hash"] != original_manifest["manifest_hash"]
    assert (
        restored_manifest["payload"]["restored_from_manifest_sha256"]
        == original_manifest["manifest_hash"]
    )
    assert restored_manifest["payload"]["run_id"] == restored_run["id"]
    assert restored_trace["root_span_id"] in {item["id"] for item in spans}
    assert (restored_trace.get("metadata") or {})["manifest_hash"] == restored_manifest[
        "manifest_hash"
    ]


def test_archive_creation_rejects_project_symlinks(context):
    project = context.catalog.create_project("归档符号链接保护")
    outside = context.paths.root.parent / "outside-secret.txt"
    outside.write_text("must not enter archive", encoding="utf-8")
    link = context.paths.project_dir(project["id"]) / "assets" / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前平台未授权创建符号链接")
    with pytest.raises(ValueError, match="ARCHIVE_PROJECT_SYMLINK_FORBIDDEN"):
        context.archives.create(project["id"], "symlink-safe-password")


def test_backup_checksum(context):
    context.catalog.create_project("备份项目")
    backup = context.backups.create()
    assert context.backups.verify(backup["id"])["valid"] is True


def test_backup_restore_is_atomic_and_keeps_emergency_recovery(context):
    retained = context.catalog.create_project("备份前项目")
    backup = context.backups.create()
    removed_by_restore = context.catalog.create_project("备份后项目")
    result = context.backups.restore(backup["id"], confirm=True)
    assert context.database.get("projects", retained["id"]) is not None
    assert context.database.get("projects", removed_by_restore["id"]) is None
    assert context.backups.verify(backup["id"])["valid"] is True
    assert context.backups.verify(result["emergency_backup_id"])["valid"] is True


def test_archive_preserves_legacy_notebook_record_and_file_without_runtime(context):
    project = context.catalog.create_project("旧 Notebook 归档兼容")
    legacy_directory = context.paths.project_dir(project["id"]) / "notebooks"
    legacy_directory.mkdir()
    path = legacy_directory / "historical.ipynb"
    path.write_text('{"cells": [], "nbformat": 4, "nbformat_minor": 5}', encoding="utf-8")
    timestamp = now_iso()
    context.database.insert(
        "notebooks",
        {
            "id": new_id("nb"),
            "project_id": project["id"],
            "dataset_version_id": None,
            "name": "历史 Notebook",
            "path": str(path),
            "kernel_id": "legacy-kernel",
            "status": "running",
            "metadata_json": {"legacy": True},
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )

    archive, _ = context.archives.create(project["id"], "legacy-notebook-password")
    restored = context.archives.restore(Path(archive["path"]), "legacy-notebook-password")
    rows = context.database.list("notebooks", {"project_id": restored["id"]}, limit=10)

    assert len(rows) == 1
    assert rows[0]["status"] == "legacy_readonly"
    assert rows[0]["kernel_id"] is None
    assert rows[0]["metadata"]["compatibility_status"] == "historical_readonly"
    restored_path = Path(rows[0]["path"])
    assert restored_path.is_file()
    assert restored_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
