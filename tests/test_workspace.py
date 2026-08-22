from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.paths import AppPaths, read_workspace_pointer, workspace_marker_path
from app.core.workspace import WorkspaceManager
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


def test_workspace_manager_does_not_switch_configured_workspace_with_projects_or_active_runs(tmp_path: Path):
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
