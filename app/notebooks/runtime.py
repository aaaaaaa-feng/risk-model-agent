from __future__ import annotations

import importlib.util
import os
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def notebook_runtime_capability() -> dict[str, Any]:
    dependencies = {
        "pandas": _module_available("pandas"),
        "numpy": _module_available("numpy"),
        "duckdb": _module_available("duckdb"),
        "nbformat": _module_available("nbformat"),
        "jupyter_client": _module_available("jupyter_client"),
        "ipykernel": _module_available("ipykernel"),
    }
    return {
        "runtime": "jupyter",
        "available": all(dependencies.values()),
        "dependencies": dependencies,
    }


@dataclass
class NotebookExecution:
    outputs: list[Any]
    execution_count: int | None
    status: str


class NotebookRuntime(ABC):
    """Execution boundary used by NotebookManager and future local runtimes."""

    @abstractmethod
    def execute(
        self,
        project_id: str,
        working_directory: Path,
        source: str,
        timeout_seconds: int,
    ) -> NotebookExecution:
        raise NotImplementedError

    @abstractmethod
    def shutdown_project(self, project_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def shutdown_all(self) -> None:
        raise NotImplementedError


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


class JupyterNotebookRuntime(NotebookRuntime):
    """One persistent local Jupyter kernel per project."""

    def __init__(self) -> None:
        self._sessions: dict[str, KernelSession] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()

    def execute(
        self,
        project_id: str,
        working_directory: Path,
        source: str,
        timeout_seconds: int,
    ) -> NotebookExecution:
        lock = self._locks.setdefault(project_id, threading.RLock())
        with lock:
            session = self._session(project_id, working_directory)
            message_id = session.client.execute(source, store_history=True, allow_stdin=False)
            try:
                outputs, execution_count, status = self._collect(
                    session.client,
                    message_id,
                    timeout_seconds,
                )
            except TimeoutError:
                # A timed-out cell must not keep consuming resources or block
                # later cells in the persistent project kernel.
                self.shutdown_project(project_id)
                raise
            session.execution_count = max(session.execution_count, execution_count or 0)
            return NotebookExecution(outputs, execution_count, status)

    def _session(self, project_id: str, working_directory: Path) -> KernelSession:
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
            client = None
            try:
                manager.start_kernel(
                    cwd=str(working_directory),
                    env=_notebook_kernel_environment(),
                    **_kernel_launch_options(),
                )
                client = manager.blocking_client()
                client.start_channels()
                client.wait_for_ready(timeout=30)
            except Exception:
                self._close_kernel(manager, client)
                raise
            session = KernelSession(manager, client)
            self._sessions[project_id] = session
            return session

    @staticmethod
    def _collect(
        client: Any,
        message_id: str,
        timeout_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> tuple[list[Any], int | None, str]:
        nbformat = _nbformat()
        outputs: list[Any] = []
        execution_count: int | None = None
        status = "succeeded"
        deadline = clock() + timeout_seconds
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("NOTEBOOK_CELL_TIMEOUT")
            try:
                message = client.get_iopub_msg(timeout=remaining)
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
                outputs.append(
                    nbformat.v4.new_output("stream", name=content["name"], text=content["text"])
                )
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
            self._close_kernel(session.manager, session.client)

    def shutdown_all(self) -> None:
        for project_id in list(self._sessions):
            self.shutdown_project(project_id)

    @staticmethod
    def _close_kernel(manager: Any, client: Any | None) -> None:
        if client is not None:
            with suppress(Exception):
                client.stop_channels()
        with suppress(Exception):
            if manager.is_alive():
                manager.shutdown_kernel(now=True)


def _nbformat() -> Any:
    try:
        import nbformat
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("NBFORMAT_DEPENDENCY_REQUIRED") from exc
    return nbformat


def _kernel_launch_options(platform_name: str | None = None) -> dict[str, Any]:
    """确保 Windows 冻结版 Notebook kernel 不创建额外控制台窗口。"""

    current = platform_name or sys.platform
    if current != "win32":
        return {}
    # subprocess.CREATE_NO_WINDOW 只在 Windows Python 暴露；常量值属于稳定的
    # Win32 CreateProcess 标志，显式写出也便于在非 Windows CI 做契约测试。
    return {"creationflags": 0x0800_0000}


def _notebook_kernel_environment(
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """Copy the runtime environment without app/provider credentials.

    Notebook code is deliberately user-controlled and is not a sandbox, but it
    still must not receive desktop control tokens or Provider secrets merely
    because the parent process owns them. Users can explicitly configure any
    environment needed by their own Notebook code.
    """

    environment = dict(source if source is not None else os.environ)
    internal_names = {
        "RISK_AGENT_API_KEY",
        "RISK_AGENT_BACKEND_LOG_PATH",
        "RISK_AGENT_DESKTOP_BOOTSTRAP_TOKEN",
        "RISK_AGENT_DESKTOP_TOKEN",
        "RISK_AGENT_INSTALL_DIR",
    }
    secret_suffixes = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
    for name in tuple(environment):
        upper = name.upper()
        if upper in internal_names or upper.endswith(secret_suffixes):
            environment.pop(name, None)
    return environment
