from __future__ import annotations

import json
import multiprocessing
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import SettingsStore, WORKER_TIMEOUT_SECONDS
from app.core.paths import AppPaths


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _pipeline_process_entry(
    root: str,
    tool: str,
    run_id: str,
    state: dict[str, Any],
    output_path: str,
) -> None:
    """Spawn target: construct only deterministic services, never another engine."""
    from app.core.database import Database
    from app.services.artifacts import ArtifactService
    from app.services.catalog import CatalogService
    from app.services.pipeline import RunPipeline

    paths = AppPaths(Path(root)).ensure()
    database = Database(paths=paths)
    catalog = CatalogService(database, paths)
    artifacts = ArtifactService(database, paths, catalog)
    pipeline = RunPipeline(database, paths, catalog, artifacts)
    destination = Path(output_path)
    temporary = destination.with_suffix(".tmp")
    try:
        result = pipeline.invoke(tool, run_id, state)
        payload = {"ok": True, "result": result}
    except BaseException as exc:  # child must report deterministic error evidence
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_code": str(exc).split(":", 1)[0][:200] or type(exc).__name__,
        }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    temporary.replace(destination)


def _score_process_entry(
    root: str,
    model_version_id: str,
    input_asset_id: str,
    output_path: str,
) -> None:
    from app.core.database import Database
    from app.services.artifacts import ArtifactService
    from app.services.catalog import CatalogService

    paths = AppPaths(Path(root)).ensure()
    database = Database(paths=paths)
    catalog = CatalogService(database, paths)
    artifacts = ArtifactService(database, paths, catalog)
    destination = Path(output_path)
    temporary = destination.with_suffix(".tmp")
    try:
        job, artifact = artifacts.score_file(model_version_id, input_asset_id)
        payload = {"ok": True, "result": {"job": job, "artifact": artifact}}
    except BaseException as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error_code": str(exc).split(":", 1)[0][:200] or type(exc).__name__,
        }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    temporary.replace(destination)


class WorkerProcessRunner:
    """Hard timeout and RSS boundary for every LangGraph tool invocation."""

    def __init__(self, paths: AppPaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._active: set[multiprocessing.Process] = set()

    def invoke(self, tool: str, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return self._run(
            _pipeline_process_entry,
            (str(self.paths.root), tool, run_id, state),
            f"tool-{tool}-{run_id[-6:]}",
        )

    def score_file(
        self, model_version_id: str, input_asset_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self._run(
            _score_process_entry,
            (str(self.paths.root), model_version_id, input_asset_id),
            f"score-{model_version_id[-6:]}",
        )
        return dict(result["job"]), dict(result["artifact"])

    def _run(self, target: Any, arguments: tuple[Any, ...], label: str) -> dict[str, Any]:
        settings = SettingsStore(self.paths).load()
        memory_limit = max(512, int(settings.memory_budget_mb)) * 1024**2
        timeout = max(1, WORKER_TIMEOUT_SECONDS)
        working = Path(
            tempfile.mkdtemp(prefix=f"risk-worker-{label}-", dir=self.paths.root)
        )
        output = working / "result.json"
        process = multiprocessing.get_context("spawn").Process(
            target=target,
            args=(*arguments, str(output)),
            name=f"risk-worker-{label}",
            daemon=False,
        )
        started = time.monotonic()
        with self._lock:
            self._active.add(process)
        try:
            process.start()
            while process.is_alive():
                process.join(0.1)
                if time.monotonic() - started > timeout:
                    self._terminate(process)
                    raise TimeoutError(f"WORKER_TIMEOUT: {label}")
                rss = _process_tree_rss(process.pid)
                if rss is not None and rss > memory_limit:
                    self._terminate(process)
                    raise MemoryError(
                        f"WORKER_MEMORY_LIMIT_EXCEEDED: {label}: {rss}/{memory_limit}"
                    )
            process.join()
            if process.exitcode != 0:
                raise RuntimeError(f"WORKER_PROCESS_EXITED: {label}: {process.exitcode}")
            if not output.is_file():
                raise RuntimeError(f"WORKER_RESULT_MISSING: {label}")
            payload = json.loads(output.read_text(encoding="utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(
                    f"{payload.get('error_code') or 'WORKER_TOOL_FAILED'}: "
                    f"{payload.get('error_type') or 'Error'}"
                )
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("WORKER_RESULT_SCHEMA_INVALID")
            return result
        finally:
            with self._lock:
                self._active.discard(process)
            if process.is_alive():
                self._terminate(process)
            shutil.rmtree(working, ignore_errors=True)

    def shutdown(self) -> None:
        with self._lock:
            processes = list(self._active)
        for process in processes:
            self._terminate(process)

    @staticmethod
    def _terminate(process: multiprocessing.Process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(5)


def _process_tree_rss(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        import psutil

        process = psutil.Process(pid)
        values = [process, *process.children(recursive=True)]
        return sum(item.memory_info().rss for item in values if item.is_running())
    except (ImportError, OSError):
        return None
    except Exception as exc:
        # psutil raises platform-specific NoSuchProcess/AccessDenied subclasses.
        if exc.__class__.__module__.startswith("psutil"):
            return None
        raise
