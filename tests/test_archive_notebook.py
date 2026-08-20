from __future__ import annotations

from pathlib import Path

import nbformat
import pytest


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
