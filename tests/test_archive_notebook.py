from __future__ import annotations

from pathlib import Path

import nbformat
import pandas as pd
import pytest

from app.core.database import new_id, now_iso
from app.api.notebooks import (
    NotebookCreate,
    NotebookImportOutput,
    create_notebook,
    import_notebook_output,
)
from app.services.archives import _remap_row
from app.notebooks.manager import NotebookManager


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


def test_project_kernel_executes_and_persists_cells(context):
    project = context.catalog.create_project("Notebook 项目")
    path = context.notebooks.create(project["id"], "nb_test", "逐单元格验证")
    document = context.notebooks.read(path)
    document["cells"].append(nbformat.v4.new_code_cell("answer = 6 * 7\nprint(answer)"))
    context.notebooks.save(path, document)
    result = context.notebooks.execute_cell(project["id"], path, 2, timeout_seconds=30)
    assert result["status"] == "succeeded"
    assert any("42" in str(output.get("text", "")) for output in result["outputs"])
    persisted = context.notebooks.read(path)
    assert persisted["cells"][2]["execution_count"] is not None


def test_default_notebook_cell_exposes_bundled_analysis_libraries(context):
    project = context.catalog.create_project("Notebook 依赖项目")
    path = context.notebooks.create(project["id"], "nb_dependencies", "依赖验证")
    result = context.notebooks.execute_cell(project["id"], path, 1, timeout_seconds=30)
    assert result["status"] == "succeeded"


def test_notebook_cell_timeout_is_wall_clock_not_per_message(monkeypatch):
    class NoisyClient:
        @staticmethod
        def get_iopub_msg(timeout):
            assert timeout > 0
            return {"parent_header": {"msg_id": "other"}, "header": {}, "content": {}}

    clock = iter([0.0, 0.2, 1.1])
    monkeypatch.setattr("app.notebooks.manager.time.monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="NOTEBOOK_CELL_TIMEOUT"):
        NotebookManager._collect(NoisyClient(), "expected", 1)


def test_notebook_output_checks_target_mapping_by_stable_business_key(context):
    project = context.catalog.create_project("Notebook 血缘项目")
    source = pd.DataFrame(
        {"order_id": ["O1", "O2", "O3", "O4"], "Y": [0, 1, 0, 1], "x": [1, 2, 3, 4]}
    )
    parent = context.catalog.create_dataset_version(
        project["id"], source, "父版本", [], {"kind": "test"}
    )
    notebook = create_notebook(
        NotebookCreate(
            project_id=project["id"],
            name="标签保护",
            dataset_version_id=parent["id"],
        ),
        context,
    )["notebook"]
    output = source.copy()
    output.loc[0, "Y"], output.loc[1, "Y"] = output.loc[1, "Y"], output.loc[0, "Y"]
    output.to_csv(Path(notebook["path"]).parent / "swapped.csv", index=False)
    with pytest.raises(ValueError, match="NOTEBOOK_OUTPUT_TARGET_MAPPING_CHANGED"):
        import_notebook_output(
            notebook["id"],
            NotebookImportOutput(
                relative_path="swapped.csv",
                label="伪造标签输出",
                parent_dataset_version_id=parent["id"],
            ),
            context,
        )
