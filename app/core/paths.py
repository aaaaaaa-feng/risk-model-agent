from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


APP_DIR_NAME = "RiskModelAgent"


def platform_data_dir() -> Path:
    override = os.getenv("RISK_AGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_DIR_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "risk-model-agent"


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
    def legacy(self) -> Path:
        return self.root / "legacy-v0"

    @property
    def config(self) -> Path:
        return self.root / "settings.json"

    def project_dir(self, project_id: str) -> Path:
        return self.projects / project_id

    def ensure(self) -> "AppPaths":
        for path in (
            self.root,
            self.projects,
            self.secrets,
            self.backups,
            self.archives,
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
    return AppPaths(platform_data_dir()).ensure()
