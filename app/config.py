from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict


BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(os.getenv("RISK_AGENT_RUNTIME_DIR", BASE_DIR / "runtime")).resolve()
DATA_DIR = RUNTIME_DIR / "projects"
SECRETS_DIR = RUNTIME_DIR / "secrets"
CONFIG_PATH = RUNTIME_DIR / "app-config.json"
MAX_UPLOAD_BYTES = int(os.getenv("RISK_AGENT_MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_BACKUP_BYTES = int(os.getenv("RISK_AGENT_MAX_BACKUP_MB", "500")) * 1024 * 1024
MAX_ROWS = int(os.getenv("RISK_AGENT_MAX_ROWS", "500000"))
MAX_COLUMNS = int(os.getenv("RISK_AGENT_MAX_COLUMNS", "20000"))
WORKER_TIMEOUT_SECONDS = int(os.getenv("RISK_AGENT_WORKER_TIMEOUT_SECONDS", "900"))
MEMORY_BUDGET_BYTES = int(os.getenv("RISK_AGENT_MEMORY_BUDGET_MB", "1536")) * 1024 * 1024
KEYRING_SERVICE = "risk-model-agent"
KEYRING_USERNAME = "provider-api-key"


def _config_defaults() -> Dict[str, Any]:
    """Return provider defaults shared by new and migrated local configs."""
    return {
        "provider": "custom",
        "api_format": "openai",
        "base_url": "",
        "model": "",
        "reviewer_model": "",
        "llm_enabled": False,
        "proxy": "",
        "ca_cert": "",
        "run_token_budget": 0,
        "monthly_token_budget": 0,
        "mode": "auto",
        "api_key_configured": bool(os.getenv("RISK_AGENT_API_KEY")),
        "secret_storage": "environment" if os.getenv("RISK_AGENT_API_KEY") else "not_configured",
    }


def ensure_runtime() -> None:
    for path in (RUNTIME_DIR, DATA_DIR, SECRETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
    try:
        SECRETS_DIR.chmod(0o700)
    except OSError:
        pass


def load_config() -> Dict[str, Any]:
    ensure_runtime()
    if not CONFIG_PATH.exists():
        return _config_defaults()
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return _config_defaults()
        defaults = _config_defaults()
        defaults.update(loaded)
        # Older V0.1 configs used an OpenAI-compatible provider label without
        # storing the wire protocol. Preserve that behavior on migration.
        defaults["api_format"] = str(defaults.get("api_format") or "openai").lower()
        return defaults
    except (OSError, json.JSONDecodeError):
        return _config_defaults()


def save_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_runtime()
    current = load_config()
    allowed = {
        "provider",
        "api_format",
        "base_url",
        "model",
        "reviewer_model",
        "llm_enabled",
        "proxy",
        "ca_cert",
        "run_token_budget",
        "monthly_token_budget",
        "mode",
    }
    current.update({key: value for key, value in payload.items() if key in allowed})
    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        storage = _save_secret(api_key)
        current["api_key_configured"] = True
        current["secret_storage"] = storage
    elif payload.get("clear_api_key"):
        _clear_secret()
        current["api_key_configured"] = False
        current["secret_storage"] = "not_configured"
    CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
    return public_config(current)


def public_config(config: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(config)
    result.pop("api_key", None)
    result["api_key"] = "" if not result.get("api_key_configured") else "••••••••"
    return result


def provider_key() -> str:
    env_key = os.getenv("RISK_AGENT_API_KEY", "").strip()
    if env_key:
        return env_key
    keyring_key = _read_keyring_secret()
    if keyring_key:
        return keyring_key
    secret_path = SECRETS_DIR / "provider_api_key"
    try:
        return secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _keyring_module() -> Any:
    try:
        import keyring

        return keyring
    except Exception:
        return None


def _read_keyring_secret() -> str:
    keyring = _keyring_module()
    if not keyring:
        return ""
    try:
        return str(keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or "").strip()
    except Exception:
        return ""


def _save_secret(api_key: str) -> str:
    keyring = _keyring_module()
    if keyring:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
            return "os-keychain"
        except Exception:
            pass
    secret_path = SECRETS_DIR / "provider_api_key"
    secret_path.write_text(api_key, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return "local-protected-file"


def _clear_secret() -> None:
    keyring = _keyring_module()
    if keyring:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass
    secret_path = SECRETS_DIR / "provider_api_key"
    if secret_path.exists():
        secret_path.unlink()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
