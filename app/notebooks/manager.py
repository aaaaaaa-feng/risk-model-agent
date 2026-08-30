from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any

from app.core.paths import AppPaths, get_paths

from .runtime import JupyterNotebookRuntime, KernelSession, NotebookRuntime

__all__ = ["KernelSession", "NotebookManager"]


class NotebookManager:
    """Owns one persistent local Python kernel per project.

    The kernel is intentionally not presented as a security sandbox. User-authored
    code and imported packages can access the network and local files with the
    permissions of the desktop application.
    """

    def __init__(
        self,
        paths: AppPaths | None = None,
        runtime: NotebookRuntime | None = None,
    ):
        self.paths = paths or get_paths()
        self.runtime = runtime or JupyterNotebookRuntime()
        self._locks: dict[str, threading.RLock] = {}

    def notebook_dir(self, project_id: str) -> Path:
        target = self.paths.project_dir(project_id) / "notebooks"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def create(self, project_id: str, notebook_id: str, name: str) -> Path:
        nbformat = _nbformat()
        notebook = nbformat.v4.new_notebook(
            metadata={
                "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                "risk_model_agent": {
                    "project_id": project_id,
                    "notebook_id": notebook_id,
                    "network_default": "enabled",
                    "security_notice": "用户代码与第三方包可能访问网络；原始数据不会由产品或 LLM 主动外发。",
                },
            }
        )
        notebook.cells = [
            nbformat.v4.new_markdown_cell(
                f"# {name}\n\n此 Notebook 在本机项目级 Kernel 中执行。执行后生成的数据仍需通过粒度、重复、样本膨胀、Y 与血缘检查。"
            ),
            nbformat.v4.new_code_cell("import pandas as pd\nimport numpy as np\nimport duckdb"),
        ]
        path = self.notebook_dir(project_id) / f"{notebook_id}.ipynb"
        nbformat.write(notebook, path)
        return path

    def read(self, path: Path) -> dict[str, Any]:
        notebook = _nbformat().read(path, as_version=4)
        return dict(notebook)

    def save(self, path: Path, notebook: dict[str, Any]) -> None:
        nbformat = _nbformat()
        validated = nbformat.from_dict(notebook)
        nbformat.validate(validated)
        nbformat.write(validated, path)

    def execute_cell(
        self,
        project_id: str,
        path: Path,
        cell_index: int,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(project_id, threading.RLock())
        with lock:
            return self._execute_cell(project_id, path, cell_index, timeout_seconds)

    def _execute_cell(
        self,
        project_id: str,
        path: Path,
        cell_index: int,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        nbformat = _nbformat()
        notebook = nbformat.read(path, as_version=4)
        if cell_index < 0 or cell_index >= len(notebook.cells):
            raise ValueError("NOTEBOOK_CELL_NOT_FOUND")
        cell = notebook.cells[cell_index]
        if cell.cell_type != "code":
            raise ValueError("NOTEBOOK_CELL_NOT_CODE")
        execution = self.runtime.execute(
            project_id,
            self.notebook_dir(project_id),
            str(cell.source),
            timeout_seconds,
        )
        cell.outputs = execution.outputs
        cell.execution_count = execution.execution_count
        nbformat.write(notebook, path)
        return {
            "cell_index": cell_index,
            "execution_count": execution.execution_count,
            "status": execution.status,
            "outputs": [dict(item) for item in execution.outputs],
        }

    def execute_all(
        self, project_id: str, path: Path, timeout_seconds: int = 300
    ) -> list[dict[str, Any]]:
        notebook = _nbformat().read(path, as_version=4)
        return [
            self.execute_cell(project_id, path, index, timeout_seconds)
            for index, cell in enumerate(notebook.cells)
            if cell.cell_type == "code"
        ]

    @staticmethod
    def _collect(
        client: Any, message_id: str, timeout_seconds: int
    ) -> tuple[list[Any], int | None, str]:
        return JupyterNotebookRuntime._collect(
            client,
            message_id,
            timeout_seconds,
            clock=time.monotonic,
        )

    def shutdown_project(self, project_id: str) -> None:
        self.runtime.shutdown_project(project_id)

    def shutdown_all(self) -> None:
        self.runtime.shutdown_all()


def _nbformat() -> Any:
    try:
        import nbformat
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("NBFORMAT_DEPENDENCY_REQUIRED") from exc
    return nbformat
