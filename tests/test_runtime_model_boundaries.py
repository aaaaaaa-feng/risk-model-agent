from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.domain.pipeline import PIPELINE_STEPS, partition_model_proposals
from app.notebooks.manager import NotebookManager
from app.notebooks.runtime import (
    JupyterNotebookRuntime,
    NotebookExecution,
    NotebookRuntime,
    _kernel_launch_options,
    _notebook_kernel_environment,
    notebook_runtime_capability,
)
from app.orchestration.contracts import TOOL_NODES
from app.services.pipeline import RunPipeline
from app.workers.model_adapters import MODEL_ADAPTERS, MODEL_REGISTRY
from app.workers.modeling import ScorecardEstimator, train_candidates


EXPECTED_MODELS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}


class RecordingRuntime(NotebookRuntime):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.closed_projects: list[str] = []
        self.closed_all = False

    def execute(
        self,
        project_id: str,
        working_directory: Path,
        source: str,
        timeout_seconds: int,
    ) -> NotebookExecution:
        self.calls.append(
            {
                "project_id": project_id,
                "working_directory": working_directory,
                "source": source,
                "timeout_seconds": timeout_seconds,
            }
        )
        return NotebookExecution(outputs=[], execution_count=7, status="succeeded")

    def shutdown_project(self, project_id: str) -> None:
        self.closed_projects.append(project_id)

    def shutdown_all(self) -> None:
        self.closed_all = True


def test_model_registry_keeps_full_algorithm_contract_and_scorecard_import_path():
    assert set(MODEL_ADAPTERS) == EXPECTED_MODELS
    assert set(MODEL_REGISTRY.availability()) == EXPECTED_MODELS
    assert all(
        spec.builder_path.startswith("app.workers.model_builders:")
        for spec in MODEL_ADAPTERS.values()
    )
    assert ScorecardEstimator.__module__ == "app.workers.modeling"


def test_pipeline_graph_and_tool_registry_share_one_step_contract(app_paths):
    pipeline = RunPipeline(paths=app_paths)

    assert TOOL_NODES is PIPELINE_STEPS
    registered = {item["name"] for item in pipeline.registry.manifest()["tools"]}
    assert registered == {step.tool_name for step in PIPELINE_STEPS}
    assert len({step.graph_node for step in PIPELINE_STEPS}) == len(PIPELINE_STEPS)
    assert all(callable(getattr(pipeline, step.handler, None)) for step in PIPELINE_STEPS)


def test_llm_model_proposals_preserve_safe_rejection_evidence():
    accepted, rejected = partition_model_proposals(
        ["xgboost", "missing_model", "Bad Model", {"name": "catboost"}, "xgboost"],
        {"xgboost": True, "catboost": True},
    )

    assert accepted == ["xgboost"]
    assert rejected == ["missing_model", "invalid_model_identifier"]


def test_notebook_manager_uses_runtime_boundary_and_default_cell_has_no_polars(app_paths):
    runtime = RecordingRuntime()
    manager = NotebookManager(app_paths, runtime=runtime)
    path = manager.create("project_runtime", "notebook_runtime", "Runtime 边界")
    document = manager.read(path)
    default_source = str(document["cells"][1]["source"])

    assert "import pandas as pd" in default_source
    assert "import numpy as np" in default_source
    assert "import duckdb" in default_source
    assert "polars" not in default_source.lower()

    result = manager.execute_cell("project_runtime", path, 1, timeout_seconds=17)
    assert result == {
        "cell_index": 1,
        "execution_count": 7,
        "status": "succeeded",
        "outputs": [],
    }
    assert runtime.calls[0]["source"] == default_source
    assert runtime.calls[0]["timeout_seconds"] == 17

    manager.shutdown_project("project_runtime")
    manager.shutdown_all()
    assert runtime.closed_projects == ["project_runtime"]
    assert runtime.closed_all is True


def test_notebook_capability_contract_is_explicit_and_excludes_polars():
    capability = notebook_runtime_capability()
    assert capability["runtime"] == "jupyter"
    assert isinstance(capability["available"], bool)
    assert set(capability["dependencies"]) == {
        "pandas",
        "numpy",
        "duckdb",
        "nbformat",
        "jupyter_client",
        "ipykernel",
    }
    assert "polars" not in capability["dependencies"]


def test_notebook_runtime_cleans_started_kernel_when_ready_check_fails(monkeypatch, tmp_path: Path):
    events: list[str] = []

    class FakeClient:
        def start_channels(self):
            events.append("channels_started")

        def wait_for_ready(self, timeout: int):
            assert timeout == 30
            raise RuntimeError("kernel not ready")

        def stop_channels(self):
            events.append("channels_stopped")

    class FakeManager:
        def __init__(self, kernel_name: str):
            assert kernel_name == "python3"
            self.client = FakeClient()

        def start_kernel(self, cwd: str, **options):
            assert cwd == str(tmp_path)
            environment = options.pop("env")
            assert "RISK_AGENT_API_KEY" not in environment
            assert options == {}
            events.append("kernel_started")

        def blocking_client(self):
            return self.client

        def is_alive(self):
            return True

        def shutdown_kernel(self, now: bool):
            assert now is True
            events.append("kernel_stopped")

    monkeypatch.setattr("jupyter_client.KernelManager", FakeManager)

    runtime = JupyterNotebookRuntime()
    with pytest.raises(RuntimeError, match="kernel not ready"):
        runtime._session("project_cleanup", tmp_path)

    assert events == [
        "kernel_started",
        "channels_started",
        "channels_stopped",
        "kernel_stopped",
    ]
    assert "project_cleanup" not in runtime._sessions


def test_notebook_runtime_hides_windows_kernel_console():
    assert _kernel_launch_options("win32") == {"creationflags": 0x0800_0000}
    assert _kernel_launch_options("darwin") == {}


def test_notebook_kernel_does_not_inherit_control_or_provider_secrets():
    environment = _notebook_kernel_environment(
        {
            "PATH": "safe-path",
            "RISK_AGENT_API_KEY": "provider-secret",
            "RISK_AGENT_DESKTOP_TOKEN": "control-secret",
            "RISK_AGENT_DESKTOP_BOOTSTRAP_TOKEN": "bootstrap-secret",
            "RISK_AGENT_BACKEND_LOG_PATH": "internal-log",
            "RISK_AGENT_INSTALL_DIR": "internal-install",
            "OPENAI_API_KEY": "external-secret",
            "CUSTOM_TOKEN": "custom-secret",
        }
    )

    assert environment == {"PATH": "safe-path"}


def test_training_preserves_explicit_evidence_for_unavailable_candidates(monkeypatch):
    frame = pd.DataFrame(
        {
            "x1": np.linspace(-2, 2, 120),
            "x2": np.cos(np.linspace(0, 6, 120)),
            "Y": (np.arange(120) % 3 == 0).astype(int),
        }
    )
    monkeypatch.setattr(
        "app.workers.modeling.available_models",
        lambda: {
            **{name: True for name in EXPECTED_MODELS},
            "random_forest": False,
        },
    )
    result, _ = train_candidates(
        frame,
        "Y",
        ["x1", "x2"],
        {
            "indices": {
                "train": np.arange(0, 80),
                "test": np.arange(80, 100),
                "oot": np.arange(100, 120),
            }
        },
        models=["unknown_model", "random_forest", "regularized_logistic"],
    )

    by_name = {item["candidate"]: item for item in result["candidates"]}
    assert by_name["unknown_model"]["error_code"] == "MODEL_UNSUPPORTED"
    assert by_name["random_forest"]["error_code"] == "MODEL_DEPENDENCY_UNAVAILABLE"
    assert by_name["regularized_logistic"]["status"] == "trained"
