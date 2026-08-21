from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    AppPaths,
    WORKSPACE_MARKER_FILE,
    WORKSPACE_POINTER_FILE,
    WORKSPACE_SCHEMA,
    is_synced_path,
    read_workspace_pointer,
    workspace_marker_path,
    workspace_pointer_path,
)


class WorkspaceManager:
    """Owns the first-run workspace pointer and its safety boundary.

    The pointer is intentionally not the data store: it only tells the next
    process start where the selected workspace lives.  Once selected, the
    active ``AppPaths.root`` contains the database, settings, backups, secrets
    and a ``projects/<project_id>`` directory for every project's files.
    """

    def status(
        self, paths: AppPaths, *, project_count: int = 0, active_run_count: int = 0
    ) -> dict[str, Any]:
        pointer = read_workspace_pointer(paths)
        env_override = self._matching_environment_override(paths)
        pointer_path = str(pointer.get("path") or "") if pointer else ""
        configured_path = Path(pointer_path).expanduser().resolve() if pointer_path else None
        active = paths.root.expanduser().resolve()
        configured = bool(env_override or (configured_path and configured_path == active))
        source = (
            "environment_override"
            if env_override
            else ("workspace_pointer" if configured else "default_control_directory")
        )
        current_path = str(active)
        selected_path = str(configured_path or active)
        marker = workspace_marker_path(active)
        return {
            "schema_version": WORKSPACE_SCHEMA,
            "configured": configured,
            "needs_setup": not configured,
            "source": source,
            "path": selected_path,
            "current_path": current_path,
            "projects_path": str(paths.projects),
            "marker_present": marker.is_file(),
            "synced_path_warning": is_synced_path(active),
            "project_count": int(project_count),
            "active_run_count": int(active_run_count),
            "pointer_path": str(workspace_pointer_path(paths)),
            "project_storage": "<workspace>/projects/<project_id>/",
        }

    def prepare(self, paths: AppPaths, requested_path: str) -> AppPaths:
        candidate = self._validate_requested_path(requested_path)
        active = paths.root.expanduser().resolve()
        if candidate == active:
            candidate.mkdir(parents=True, exist_ok=True)
            self._ensure_marker(candidate)
            return AppPaths(candidate, control_root=paths.control_root or paths.root)

        candidate.mkdir(parents=True, exist_ok=True)
        self._ensure_marker(candidate)
        return AppPaths(candidate, control_root=paths.control_root or paths.root)

    def persist(self, paths: AppPaths, selected: AppPaths) -> None:
        if self._matching_environment_override(paths):
            raise ValueError("WORKSPACE_CONFIGURED_BY_ENVIRONMENT")
        pointer = workspace_pointer_path(paths)
        pointer.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": WORKSPACE_SCHEMA,
            "path": str(selected.root),
            "marker": WORKSPACE_MARKER_FILE,
        }
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{WORKSPACE_POINTER_FILE}.", dir=pointer.parent
        )
        os.close(file_descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(pointer)
        finally:
            temporary.unlink(missing_ok=True)

    def select(
        self,
        paths: AppPaths,
        requested_path: str,
        *,
        project_count: int = 0,
        active_run_count: int = 0,
    ) -> AppPaths:
        candidate = self._validate_requested_path(requested_path)
        if candidate != paths.root.expanduser().resolve() and (project_count or active_run_count):
            raise ValueError("WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS")
        selected = self.prepare(paths, str(candidate))
        self.persist(paths, selected)
        return selected

    @staticmethod
    def _validate_requested_path(requested_path: str) -> Path:
        raw = str(requested_path or "").strip()
        if not raw or len(raw) > 4096:
            raise ValueError("WORKSPACE_PATH_REQUIRED")
        candidate = Path(raw).expanduser().resolve()
        if candidate in {Path(candidate.anchor), Path.home().resolve()}:
            raise ValueError("WORKSPACE_PATH_TOO_BROAD")
        if candidate.exists() and not candidate.is_dir():
            raise ValueError("WORKSPACE_PATH_NOT_DIRECTORY")
        return candidate

    @staticmethod
    def _matching_environment_override(paths: AppPaths) -> str | None:
        raw = (
            os.getenv("RISK_AGENT_DATA_DIR", "").strip()
            or os.getenv("RISK_AGENT_WORKSPACE_DIR", "").strip()
        )
        if not raw:
            return None
        candidate = Path(raw).expanduser().resolve()
        active = paths.root.expanduser().resolve()
        control = (paths.control_root or paths.root).expanduser().resolve()
        # A caller that supplied an explicit AppPaths (notably tests and
        # embedded hosts) must not be hijacked by the process-wide override.
        return raw if candidate in {active, control} else None

    @staticmethod
    def _ensure_marker(root: Path) -> None:
        marker = workspace_marker_path(root)
        if marker.exists():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                raise ValueError("WORKSPACE_MARKER_INVALID")
            if not isinstance(payload, dict) or payload.get("schema_version") != WORKSPACE_SCHEMA:
                raise ValueError("WORKSPACE_MARKER_INVALID")
            return
        payload = {
            "schema_version": WORKSPACE_SCHEMA,
            "app": "Risk Model Agent",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = marker.with_name(f".{marker.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(marker)
        finally:
            temporary.unlink(missing_ok=True)


def pick_workspace_directory() -> str | None:
    """Open a native folder picker when the desktop platform provides one.

    The web UI keeps a typed-path fallback.  All commands use argument lists or
    fixed scripts; the user-selected path never becomes shell source.
    """

    system = platform.system()
    try:
        if system == "Windows":
            executable = shutil.which("powershell.exe") or shutil.which("powershell")
            if not executable:
                raise RuntimeError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$dialog.Description = '选择风控建模 Agent 工作文件夹'; "
                "$dialog.ShowNewFolderButton = $true; "
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
                "{ $dialog.SelectedPath }"
            )
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        elif system == "Darwin":
            executable = shutil.which("osascript")
            if not executable:
                raise RuntimeError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            result = subprocess.run(
                [
                    executable,
                    "-e",
                    'POSIX path of (choose folder with prompt "选择风控建模 Agent 工作文件夹")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        else:
            executable = shutil.which("zenity")
            if not executable:
                raise RuntimeError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            result = subprocess.run(
                [
                    executable,
                    "--file-selection",
                    "--directory",
                    "--title=选择风控建模 Agent 工作文件夹",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("WORKSPACE_NATIVE_PICKER_FAILED") from exc
    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    return selected or None
