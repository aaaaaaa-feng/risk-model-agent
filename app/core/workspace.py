from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
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
    validate_workspace_root,
    workspace_marker_path,
    workspace_pointer_path,
)


NATIVE_PICKER_TIMEOUT_SECONDS = 60
_NATIVE_PICKER_LOCK = threading.Lock()


class WorkspacePickerError(RuntimeError):
    """A native-picker failure that is safe to expose to the local UI."""

    _MESSAGES = {
        "WORKSPACE_NATIVE_PICKER_BUSY": "系统文件夹选择器已在打开，请先完成或取消前一个窗口。",
        "WORKSPACE_NATIVE_PICKER_UNAVAILABLE": "当前系统没有可用的文件夹选择器，请直接输入完整路径。",
        "WORKSPACE_NATIVE_PICKER_TIMEOUT": "等待系统文件夹窗口超时，请重试或直接输入完整路径。",
        "WORKSPACE_NATIVE_PICKER_FAILED": "系统文件夹选择器打开失败，请重试或直接输入完整路径。",
    }

    def __init__(self, code: str) -> None:
        self.code = code
        self.public_message = self._MESSAGES.get(
            code, self._MESSAGES["WORKSPACE_NATIVE_PICKER_FAILED"]
        )
        super().__init__(code)


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
            self._assert_writable(candidate)
            self._ensure_marker(candidate)
            return AppPaths(candidate, control_root=paths.control_root or paths.root)

        candidate.mkdir(parents=True, exist_ok=True)
        self._assert_writable(candidate)
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
        try:
            candidate = self._validate_requested_path(requested_path)
            active = paths.root.expanduser().resolve()
            if candidate != active:
                # A first-run install may still have projects in the temporary
                # application directory (for example, data created before the
                # workspace picker was introduced).  Selecting the real workspace
                # must remain possible; those old projects stay in the old
                # directory and are never deleted or silently moved.
                if active_run_count:
                    raise ValueError("WORKSPACE_SWITCH_ACTIVE_RUNS")
                if project_count and not self._is_initial_control_context(paths):
                    raise ValueError("WORKSPACE_SWITCH_REQUIRES_EMPTY_CURRENT_PROJECTS")
            selected = self.prepare(paths, str(candidate))
            self.persist(paths, selected)
            return selected
        except OSError as exc:
            # Filesystem permission/locking errors must never surface as an
            # opaque HTTP 500 during first-run setup.  Keep the original error
            # chained for local diagnostics while exposing a stable code.
            raise ValueError("WORKSPACE_PATH_NOT_WRITABLE") from exc

    def _is_initial_control_context(self, paths: AppPaths) -> bool:
        """Return whether ``paths`` is the unconfigured first-run context."""

        active = paths.root.expanduser().resolve()
        control = (paths.control_root or paths.root).expanduser().resolve()
        if active != control or self._matching_environment_override(paths):
            return False
        return not self.status(paths).get("configured", False)

    @staticmethod
    def _validate_requested_path(requested_path: str) -> Path:
        raw = str(requested_path or "").strip()
        if not raw or len(raw) > 4096:
            raise ValueError("WORKSPACE_PATH_REQUIRED")
        candidate = validate_workspace_root(raw)
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
    def _assert_writable(root: Path) -> None:
        descriptor, probe_name = tempfile.mkstemp(prefix=".risk-model-agent-write-probe-", dir=root)
        os.close(descriptor)
        Path(probe_name).unlink(missing_ok=True)

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

    if not _NATIVE_PICKER_LOCK.acquire(blocking=False):
        raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_BUSY")

    try:
        system = platform.system()
        if system == "Windows":
            executable = (
                shutil.which("powershell.exe")
                or shutil.which("pwsh.exe")
                or shutil.which("powershell")
                or shutil.which("pwsh")
            )
            if not executable:
                raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            script = (
                "$ErrorActionPreference = 'Stop'; "
                "$utf8 = New-Object System.Text.UTF8Encoding($false); "
                "[Console]::OutputEncoding = $utf8; $OutputEncoding = $utf8; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Application]::EnableVisualStyles(); "
                "$owner = New-Object System.Windows.Forms.Form; "
                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$exitCode = 1; "
                "try { "
                "$owner.Text = 'Risk Model Agent'; "
                "$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen; "
                "$owner.Size = New-Object System.Drawing.Size(1, 1); "
                "$owner.ShowInTaskbar = $false; $owner.TopMost = $true; $owner.Opacity = 0; "
                "$dialog.Description = '选择风控建模 Agent 工作文件夹'; "
                "$dialog.ShowNewFolderButton = $true; "
                "$null = $owner.Show(); $null = $owner.Activate(); "
                "$choice = $dialog.ShowDialog($owner); "
                "if ($choice -eq [System.Windows.Forms.DialogResult]::OK) { "
                "[Console]::Out.Write($dialog.SelectedPath); $exitCode = 0 "
                "} else { $exitCode = 2 } "
                "} catch { [Console]::Error.Write($_.Exception.Message); $exitCode = 1 } "
                "finally { $dialog.Dispose(); $owner.Dispose() }; "
                "exit $exitCode"
            )
            encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-STA",
                    "-EncodedCommand",
                    encoded_script,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=NATIVE_PICKER_TIMEOUT_SECONDS,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cancelled = result.returncode == 2
        elif system == "Darwin":
            executable = shutil.which("osascript")
            if not executable:
                raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            result = subprocess.run(
                [
                    executable,
                    "-e",
                    'POSIX path of (choose folder with prompt "选择风控建模 Agent 工作文件夹")',
                ],
                capture_output=True,
                text=True,
                timeout=NATIVE_PICKER_TIMEOUT_SECONDS,
                check=False,
            )
            cancelled = result.returncode == 1 and "(-128)" in result.stderr
        else:
            executable = shutil.which("zenity")
            if not executable:
                raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_UNAVAILABLE")
            result = subprocess.run(
                [
                    executable,
                    "--file-selection",
                    "--directory",
                    "--title=选择风控建模 Agent 工作文件夹",
                ],
                capture_output=True,
                text=True,
                timeout=NATIVE_PICKER_TIMEOUT_SECONDS,
                check=False,
            )
            cancelled = result.returncode == 1
    except subprocess.TimeoutExpired as exc:
        raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_TIMEOUT") from exc
    except WorkspacePickerError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_FAILED") from exc
    finally:
        _NATIVE_PICKER_LOCK.release()
    if result.returncode != 0:
        if cancelled:
            return None
        raise WorkspacePickerError("WORKSPACE_NATIVE_PICKER_FAILED")
    selected = result.stdout.strip()
    return selected or None
