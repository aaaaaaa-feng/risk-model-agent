from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.paths import AppPaths, read_workspace_pointer, workspace_marker_path
from app.core.workspace import WorkspaceManager, WorkspacePickerError, pick_workspace_directory
from app.main import create_app


def test_workspace_manager_persists_pointer_and_project_boundary(tmp_path: Path):
    paths = AppPaths(tmp_path / "control").ensure()
    target = tmp_path / "chosen-workspace"
    manager = WorkspaceManager()

    selected = manager.select(paths, str(target))
    assert selected.root == target.resolve()
    assert workspace_marker_path(selected.root).is_file()
    assert read_workspace_pointer(paths)["path"] == str(target.resolve())
    status = manager.status(selected)
    assert status["configured"] is True
    assert status["needs_setup"] is False
    assert status["project_storage"] == "<workspace>/projects/<project_id>/"


def test_workspace_manager_allows_first_selection_with_existing_control_projects(tmp_path: Path):
    paths = AppPaths(tmp_path / "control").ensure()
    manager = WorkspaceManager()
    selected = manager.select(paths, str(tmp_path / "first-workspace"), project_count=1)
    assert selected.root == (tmp_path / "first-workspace").resolve()
    assert not (tmp_path / "first-workspace" / "projects" / "old-project").exists()


def test_workspace_manager_does_not_switch_configured_workspace_with_projects_or_active_runs(
    tmp_path: Path,
):
    paths = AppPaths(tmp_path / "control").ensure()
    manager = WorkspaceManager()
    selected = manager.select(paths, str(tmp_path / "configured-workspace"))
    with pytest.raises(ValueError, match="WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS"):
        manager.select(selected, str(tmp_path / "other"), project_count=1)
    with pytest.raises(ValueError, match="WORKSPACE_SWITCH_ACTIVE_RUNS"):
        manager.select(selected, str(tmp_path / "running"), active_run_count=1)
    assert not (tmp_path / "other" / ".risk-model-agent-workspace.json").exists()


def test_workspace_api_switches_context_and_project_folder_is_self_describing(tmp_path: Path):
    app = create_app(AppPaths(tmp_path / "control").ensure(), auto_migrate=False)
    chosen = tmp_path / "selected"
    with TestClient(app) as client:
        first = client.get("/api/v1/workspace")
        assert first.status_code == 200
        assert first.json()["workspace"]["needs_setup"] is True

        switched = client.post("/api/v1/workspace/select", json={"path": str(chosen)})
        assert switched.status_code == 200
        assert switched.json()["switched"] is True
        assert switched.json()["workspace"]["configured"] is True
        assert client.get("/api/v1/health").json()["data_directory"] == str(chosen.resolve())

        created = client.post("/api/v1/projects", json={"name": "工作区项目"})
        assert created.status_code == 201
        project_id = created.json()["project"]["id"]
        project_dir = chosen / "projects" / project_id
        assert (project_dir / ".risk-model-agent-project.json").is_file()
        assert (project_dir / "assets").is_dir()
        assert (project_dir / "runs").is_dir()
        assert (project_dir / "notebooks").is_dir()

        blocked = client.post("/api/v1/workspace/select", json={"path": str(tmp_path / "another")})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS"


def test_workspace_api_returns_actionable_error_for_unwritable_directory(
    tmp_path: Path, monkeypatch
):
    def not_writable(_root: Path) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(
        WorkspaceManager,
        "_assert_writable",
        staticmethod(not_writable),
    )
    app = create_app(AppPaths(tmp_path / "control").ensure(), auto_migrate=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workspace/select",
            json={"path": str(tmp_path / "read-only")},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKSPACE_PATH_NOT_WRITABLE"
    assert "不可写" in response.json()["error"]["message"]


def test_windows_picker_is_sta_owned_topmost_and_keeps_unicode_path(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.core.workspace.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.core.workspace.shutil.which", lambda _: "powershell.exe")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=r"D:\\风控建模", stderr="")

    monkeypatch.setattr("app.core.workspace.subprocess.run", fake_run)

    assert pick_workspace_directory() == r"D:\\风控建模"
    command = captured["command"]
    assert isinstance(command, list)
    assert "-STA" in command
    encoded = command[command.index("-EncodedCommand") + 1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    assert "$owner.TopMost = $true" in script
    assert "$dialog.ShowDialog($owner)" in script
    assert "$null = $owner.Activate()" in script
    assert captured["kwargs"]["timeout"] == 60
    assert captured["kwargs"]["encoding"] == "utf-8"


def test_windows_picker_distinguishes_cancel_from_startup_failure(monkeypatch):
    monkeypatch.setattr("app.core.workspace.platform.system", lambda: "Windows")
    monkeypatch.setattr("app.core.workspace.shutil.which", lambda _: "powershell.exe")
    monkeypatch.setattr(
        "app.core.workspace.subprocess.run",
        lambda *_, **__: SimpleNamespace(returncode=2, stdout="", stderr=""),
    )
    assert pick_workspace_directory() is None

    monkeypatch.setattr(
        "app.core.workspace.subprocess.run",
        lambda *_, **__: SimpleNamespace(returncode=1, stdout="", stderr="failed"),
    )
    with pytest.raises(WorkspacePickerError, match="WORKSPACE_NATIVE_PICKER_FAILED"):
        pick_workspace_directory()


def test_native_picker_timeout_returns_actionable_api_error(tmp_path: Path, monkeypatch):
    def timeout_picker():
        raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_TIMEOUT")

    monkeypatch.setattr("app.api.workspace.pick_workspace_directory", timeout_picker)
    app = create_app(AppPaths(tmp_path / "control").ensure(), auto_migrate=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/workspace/native-picker", json={})

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "WORKSPACE_NATIVE_PICKER_TIMEOUT"
    assert "超时" in response.json()["error"]["message"]


def test_picker_subprocess_timeout_has_specific_error(monkeypatch):
    monkeypatch.setattr("app.core.workspace.platform.system", lambda: "Darwin")
    monkeypatch.setattr("app.core.workspace.shutil.which", lambda _: "osascript")

    def fake_run(*_, **__):
        raise subprocess.TimeoutExpired(cmd=["osascript"], timeout=60)

    monkeypatch.setattr("app.core.workspace.subprocess.run", fake_run)
    with pytest.raises(WorkspacePickerError, match="WORKSPACE_NATIVE_PICKER_TIMEOUT"):
        pick_workspace_directory()
