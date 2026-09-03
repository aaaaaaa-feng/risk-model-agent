from __future__ import annotations

import os
import platform
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_DIR_NAME = "RiskModelAgent"
WORKSPACE_POINTER_FILE = "workspace-selection.json"
WORKSPACE_MARKER_FILE = ".risk-model-agent-workspace.json"
PROJECT_MARKER_FILE = ".risk-model-agent-project.json"
WORKSPACE_SCHEMA = "risk-agent-workspace/v1"


def validate_workspace_root(path: str | Path) -> Path:
    """Resolve one workspace root and enforce the process-wide safety boundary.

    This validator is intentionally shared by first-run selection and startup
    resolution.  A pointer or environment override must never bypass the same
    install-tree and broad-directory rules enforced by the API.
    """

    candidate = Path(path).expanduser().resolve()
    if candidate in {Path(candidate.anchor), Path.home().resolve()}:
        raise ValueError("WORKSPACE_PATH_TOO_BROAD")
    install_directory = os.getenv("RISK_AGENT_INSTALL_DIR", "").strip()
    if install_directory:
        protected = Path(install_directory).expanduser().resolve()
        if (
            candidate == protected
            or protected in candidate.parents
            or candidate in protected.parents
        ):
            raise ValueError("WORKSPACE_PATH_INSIDE_INSTALLATION")
    return candidate


def _has_valid_workspace_marker(root: Path) -> bool:
    try:
        payload = json.loads(workspace_marker_path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("schema_version") == WORKSPACE_SCHEMA


def _default_platform_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_DIR_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "risk-model-agent"


def platform_data_dir() -> Path:
    override = os.getenv("RISK_AGENT_DATA_DIR", "").strip()
    if override:
        return validate_workspace_root(override)
    return _default_platform_data_dir()


def is_synced_path(path: Path) -> bool:
    lowered = "/".join(part.lower() for part in path.expanduser().resolve().parts)
    markers = (
        "icloud",
        "mobile documents",
        "onedrive",
        "dropbox",
        "google drive",
        "baidunetdisk",
    )
    return any(marker in lowered for marker in markers)


@dataclass(frozen=True)
class AppPaths:
    root: Path
    # The pointer is kept in a small application-control directory so the
    # selected workspace can be resolved before the main context/database is
    # opened.  Tests and explicit RISK_AGENT_DATA_DIR overrides use root for
    # both locations and therefore remain completely isolated.
    control_root: Path | None = None

    @property
    def database(self) -> Path:
        return self.root / "risk_model_agent_v1.sqlite3"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def archives(self) -> Path:
        return self.root / "archives"

    @property
    def evaluations(self) -> Path:
        """Local Harness output; never sent to a provider by the app."""
        return self.root / "evaluations"

    @property
    def legacy(self) -> Path:
        return self.root / "legacy-v0"

    @property
    def config(self) -> Path:
        return self.root / "settings.json"

    @property
    def provider_profiles(self) -> Path:
        """Non-secret Provider configurations kept alongside app settings."""
        return self.root / "provider_profiles.json"

    def project_dir(self, project_id: str) -> Path:
        return self.projects / project_id

    def project_manifest(self, project_id: str) -> Path:
        return self.project_dir(project_id) / PROJECT_MARKER_FILE

    def ensure(self) -> "AppPaths":
        for path in (
            self.root,
            self.projects,
            self.secrets,
            self.backups,
            self.archives,
            self.evaluations,
            self.legacy,
        ):
            path.mkdir(parents=True, exist_ok=True)
        for protected in (self.root, self.secrets):
            try:
                protected.chmod(0o700)
            except OSError:
                pass
        return self


def get_paths() -> AppPaths:
    explicit_data_dir = os.getenv("RISK_AGENT_DATA_DIR", "").strip()
    if explicit_data_dir:
        root = validate_workspace_root(explicit_data_dir)
        return AppPaths(root, control_root=root).ensure()

    control_root = _default_platform_data_dir().resolve()
    explicit_workspace = os.getenv("RISK_AGENT_WORKSPACE_DIR", "").strip()
    selected = validate_workspace_root(explicit_workspace) if explicit_workspace else None
    if selected is None:
        pointer = control_root / WORKSPACE_POINTER_FILE
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != WORKSPACE_SCHEMA:
                raise ValueError("WORKSPACE_POINTER_INVALID")
            raw_candidate = str(payload.get("path") or "").strip()
            if not raw_candidate:
                raise ValueError("WORKSPACE_POINTER_INVALID")
            candidate = validate_workspace_root(raw_candidate)
            # A moved/removed workspace is not silently recreated.  The app
            # remains on the control directory and the UI asks for a new one.
            if candidate.is_dir() and _has_valid_workspace_marker(candidate):
                selected = candidate
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            selected = None
    return AppPaths(selected or control_root, control_root=control_root).ensure()


def workspace_pointer_path(paths: AppPaths | None = None) -> Path:
    target = paths or get_paths()
    return (target.control_root or target.root) / WORKSPACE_POINTER_FILE


def workspace_marker_path(root: Path) -> Path:
    return root / WORKSPACE_MARKER_FILE


def read_workspace_pointer(paths: AppPaths | None = None) -> dict[str, Any] | None:
    pointer = workspace_pointer_path(paths)
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != WORKSPACE_SCHEMA:
        return None
    if not str(value.get("path") or "").strip():
        return None
    return value
