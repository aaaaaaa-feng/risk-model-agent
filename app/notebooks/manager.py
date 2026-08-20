from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.paths import AppPaths, get_paths


@dataclass
class KernelSession:
    manager: Any
    client: Any
    execution_count: int = 0


class _BundledLocalProvisionerEntryPoint:
    """Stable entry-point adapter for frozen builds with metadata shims."""

    @staticmethod
    def load() -> Any:
        from jupyter_client.provisioning.local_provisioner import LocalProvisioner

        return LocalProvisioner


class NotebookManager:
    """Owns one persistent local Python kernel per project.

    The kernel is intentionally not presented as a security sandbox. User-authored
    code and imported packages can access the network and local files with the
    permissions of the desktop application.
    """

    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_paths()
        self._sessions: dict[str, KernelSession] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()

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
            nbformat.v4.new_code_cell(
                "import pandas as pd\n"
                "import numpy as np\n"
                "import polars as pl\n"
                "import duckdb"
            ),
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
            nbformat = _nbformat()
            notebook = nbformat.read(path, as_version=4)
            if cell_index < 0 or cell_index >= len(notebook.cells):
                raise ValueError("NOTEBOOK_CELL_NOT_FOUND")
            cell = notebook.cells[cell_index]
            if cell.cell_type != "code":
                raise ValueError("NOTEBOOK_CELL_NOT_CODE")
            session = self._session(project_id)
            message_id = session.client.execute(cell.source, store_history=True, allow_stdin=False)
            outputs, execution_count, status = self._collect(
                session.client, message_id, timeout_seconds
            )
            cell.outputs = outputs
            cell.execution_count = execution_count
            session.execution_count = max(session.execution_count, execution_count or 0)
            nbformat.write(notebook, path)
            return {
                "cell_index": cell_index,
                "execution_count": execution_count,
                "status": status,
                "outputs": [dict(item) for item in outputs],
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

    def _session(self, project_id: str) -> KernelSession:
        with self._global_lock:
            existing = self._sessions.get(project_id)
            if existing and existing.manager.is_alive():
                return existing
            try:
                from jupyter_client import KernelManager
                from jupyter_client.provisioning.factory import KernelProvisionerFactory
            except ImportError as exc:  # pragma: no cover - dependency contract
                raise RuntimeError("JUPYTER_CLIENT_DEPENDENCY_REQUIRED") from exc
            provisioners = KernelProvisionerFactory.instance().provisioners
            provisioners["local-provisioner"] = _BundledLocalProvisionerEntryPoint()
            manager = KernelManager(kernel_name="python3")
            manager.start_kernel(cwd=str(self.notebook_dir(project_id)))
            client = manager.blocking_client()
            client.start_channels()
            client.wait_for_ready(timeout=30)
            session = KernelSession(manager, client)
            self._sessions[project_id] = session
            return session

    @staticmethod
    def _collect(client: Any, message_id: str, timeout_seconds: int) -> tuple[list[Any], int | None, str]:
        nbformat = _nbformat()
        outputs: list[Any] = []
        execution_count: int | None = None
        status = "succeeded"
        while True:
            try:
                message = client.get_iopub_msg(timeout=timeout_seconds)
            except queue.Empty as exc:
                raise TimeoutError("NOTEBOOK_CELL_TIMEOUT") from exc
            if message.get("parent_header", {}).get("msg_id") != message_id:
                continue
            message_type = message["header"]["msg_type"]
            content = message["content"]
            if message_type == "status" and content.get("execution_state") == "idle":
                break
            if message_type == "execute_input":
                execution_count = content.get("execution_count")
            elif message_type == "stream":
                outputs.append(nbformat.v4.new_output("stream", name=content["name"], text=content["text"]))
            elif message_type in {"display_data", "execute_result"}:
                outputs.append(
                    nbformat.v4.new_output(
                        message_type,
                        data=content.get("data", {}),
                        metadata=content.get("metadata", {}),
                        execution_count=content.get("execution_count"),
                    )
                )
            elif message_type == "error":
                status = "failed"
                outputs.append(
                    nbformat.v4.new_output(
                        "error",
                        ename=content.get("ename", "Error"),
                        evalue=content.get("evalue", ""),
                        traceback=content.get("traceback", []),
                    )
                )
        return outputs, execution_count, status

    def shutdown_project(self, project_id: str) -> None:
        with self._global_lock:
            session = self._sessions.pop(project_id, None)
        if session:
            try:
                session.client.stop_channels()
                session.manager.shutdown_kernel(now=True)
            except Exception:
                pass

    def shutdown_all(self) -> None:
        for project_id in list(self._sessions):
            self.shutdown_project(project_id)


def _nbformat() -> Any:
    try:
        import nbformat
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("NBFORMAT_DEPENDENCY_REQUIRED") from exc
    return nbformat
