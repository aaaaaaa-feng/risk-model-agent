"""Application settings with safe local-only defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    instance_dir: Path
    database_path: Path
    max_upload_bytes: int = 15 * 1024 * 1024
    max_rows: int = 50_000
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        configured = os.getenv("RISK_AGENT_INSTANCE_DIR")
        instance_dir = Path(configured).expanduser() if configured else PROJECT_ROOT / "instance"
        instance_dir = instance_dir.resolve()
        return cls(instance_dir=instance_dir, database_path=instance_dir / "risk_agent.sqlite3")


settings = Settings.from_env()
