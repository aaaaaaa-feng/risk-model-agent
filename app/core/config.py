from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import AppPaths, get_paths, is_synced_path


PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "api_format": "openai",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    },
    "kimi": {
        "label": "Kimi 开放平台",
        "api_format": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.6",
    },
    "kimi-code": {
        "label": "Kimi Code",
        "api_format": "openai",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "kimi-for-coding",
    },
    "openai": {
        "label": "OpenAI",
        "api_format": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
    },
    "anthropic": {
        "label": "Anthropic",
        "api_format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
    },
    "custom": {"label": "自定义", "api_format": "openai", "base_url": "", "model": ""},
}


@dataclass
class Settings:
    provider: str = "deepseek"
    api_format: str = "openai"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    reviewer_model: str = ""
    # The product defaults to LLM enabled.  ProviderGateway still fails closed
    # when no key/base URL/model is configured, so this does not cause a
    # network request on a fresh install.
    llm_enabled: bool = True
    mode: str = "semi_trusted"
    run_token_budget: int = 0
    monthly_token_budget: int = 0
    proxy: str = ""
    ca_cert: str = ""
    notebook_network: bool = True
    telemetry: bool = False
    auto_update: bool = True
    memory_budget_mb: int = 1536
    max_parallel_models: int = 1
    default_models: list[str] | None = None
    api_key_configured: bool = False
    secret_storage: str = "not_configured"

    def __post_init__(self) -> None:
        if self.default_models is None:
            self.default_models = ["dummy", "scorecard", "regularized_logistic", "xgboost"]

    def public(self, paths: AppPaths | None = None) -> dict[str, Any]:
        target = paths or get_paths()
        value = asdict(self)
        value["api_key"] = "••••••••" if self.api_key_configured else ""
        value["data_dir"] = str(target.root)
        value["synced_path_warning"] = is_synced_path(target.root)
        return value


class SettingsStore:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_paths()

    def load(self) -> Settings:
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(self.paths.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        allowed = {item.name for item in fields(Settings)}
        settings = Settings(**{key: value for key, value in raw.items() if key in allowed})
        # Configurations written by versions before the default changed have
        # an explicit-looking false value but no marker that the user chose to
        # disable LLM.  Once a key is present, migrate that legacy default to
        # enabled.  A later explicit save of False records the marker below
        # and is respected thereafter.
        if (
            raw.get("llm_enabled") is False
            and raw.get("api_key_configured") is True
            and raw.get("_llm_enabled_explicit") is not True
        ):
            settings.llm_enabled = True
        if os.getenv("RISK_AGENT_API_KEY", "").strip():
            settings.api_key_configured = True
            settings.secret_storage = "environment"
        return settings

    def save(self, payload: dict[str, Any]) -> Settings:
        current = self.load()
        immutable = {"api_key_configured", "secret_storage"}
        for item in fields(Settings):
            if item.name in payload and item.name not in immutable:
                setattr(current, item.name, payload[item.name])
        value = asdict(current)
        if "llm_enabled" in payload:
            value["_llm_enabled_explicit"] = True
        self._write(value)
        return current

    def save_secret_state(self, configured: bool, storage: str) -> Settings:
        current = self.load()
        current.api_key_configured = configured
        current.secret_storage = storage
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(self.paths.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        value = asdict(current)
        if raw.get("_llm_enabled_explicit") is True:
            value["_llm_enabled_explicit"] = True
        self._write(value)
        return current

    def reset(self, preserve_secret_state: bool = True) -> Settings:
        previous = self.load()
        reset = Settings()
        if preserve_secret_state:
            reset.api_key_configured = previous.api_key_configured
            reset.secret_storage = previous.secret_storage
        self._write(asdict(reset))
        return reset

    def _write(self, value: dict[str, Any]) -> None:
        self.paths.config.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            self.paths.config.chmod(0o600)
        except OSError:
            pass


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_UPLOAD_BYTES = env_int("RISK_AGENT_MAX_UPLOAD_MB", 2048) * 1024 * 1024
MAX_ARCHIVE_BYTES = env_int("RISK_AGENT_MAX_ARCHIVE_MB", 8192) * 1024 * 1024
WORKER_TIMEOUT_SECONDS = env_int("RISK_AGENT_WORKER_TIMEOUT_SECONDS", 3600)


def safe_local_path(path: str | Path, root: Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    root = root.expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("PATH_OUTSIDE_APPLICATION_DATA")
    return candidate
