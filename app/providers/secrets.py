from __future__ import annotations

import os
from typing import Any

from app.core.paths import AppPaths, get_paths


SERVICE = "risk-model-agent-v1"
USERNAME = "provider-api-key"


def _keyring() -> Any:
    try:
        import keyring

        return keyring
    except Exception:
        return None


class SecretStore:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_paths()
        self.local_path = self.paths.secrets / "provider_api_key"

    def read(self) -> str:
        environment = os.getenv("RISK_AGENT_API_KEY", "").strip()
        if environment:
            return environment
        keyring = _keyring()
        if keyring:
            try:
                value = str(keyring.get_password(SERVICE, USERNAME) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        try:
            return self.local_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def save(self, value: str) -> str:
        if os.getenv("RISK_AGENT_API_KEY", "").strip():
            return "environment"
        value = value.strip()
        if not value:
            raise ValueError("EMPTY_API_KEY")
        keyring = _keyring()
        if keyring:
            try:
                keyring.set_password(SERVICE, USERNAME, value)
                self._remove_local()
                return "os-keychain"
            except Exception:
                pass
        self.local_path.write_text(value, encoding="utf-8")
        try:
            self.local_path.chmod(0o600)
        except OSError:
            pass
        return "local-protected-file"

    def clear(self) -> str:
        keyring = _keyring()
        if keyring:
            try:
                keyring.delete_password(SERVICE, USERNAME)
            except Exception:
                pass
        self._remove_local()
        return "environment" if os.getenv("RISK_AGENT_API_KEY", "").strip() else "not_configured"

    def _remove_local(self) -> None:
        try:
            self.local_path.unlink(missing_ok=True)
        except TypeError:  # Python 3.9 compatibility for local verification
            if self.local_path.exists():
                self.local_path.unlink()
