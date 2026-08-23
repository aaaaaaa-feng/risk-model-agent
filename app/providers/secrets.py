from __future__ import annotations

import os
import re
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
    def __init__(self, paths: AppPaths | None = None, profile_id: str | None = None):
        self.paths = paths or get_paths()
        self.profile_id = _safe_profile_id(profile_id) if profile_id else ""
        if self.profile_id:
            self.local_path = self.paths.secrets / "providers" / f"{self.profile_id}.key"
            self.username = f"{USERNAME}:{self.profile_id}"
        else:
            self.local_path = self.paths.secrets / "provider_api_key"
            self.username = USERNAME

    def read(self) -> str:
        environment = os.getenv("RISK_AGENT_API_KEY", "").strip()
        if environment:
            return environment
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
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.write_text(value, encoding="utf-8")
        try:
            self.local_path.chmod(0o600)
        except OSError:
            pass
        return "local-protected-file"

    def clear(self, include_legacy: bool = False) -> str:
        self._remove_local()
        if include_legacy and self.profile_id:
            SecretStore(self.paths).clear()
        return "environment" if os.getenv("RISK_AGENT_API_KEY", "").strip() else "not_configured"

    def migrate_keyring_to_local(self, *, remove_source: bool = True) -> bool:
        """Move a legacy keyring value to local storage once.

        Normal application reads never touch the OS keychain.  This explicit
        migration is kept for upgrades where an older version stored the key
        there.  The value is never returned or logged; after a successful
        local write the old keyring entry is removed by default.
        """

        if os.getenv("RISK_AGENT_API_KEY", "").strip() or self.read():
            return False
        keyring = _keyring()
        if not keyring:
            return False
        try:
            value = str(keyring.get_password(SERVICE, self.username) or "").strip()
        except Exception:
            return False
        if not value:
            return False
        self.save(value)
        if remove_source:
            try:
                keyring.delete_password(SERVICE, self.username)
            except Exception:
                # Local storage is already authoritative.  A platform may
                # refuse deletion, but normal reads still never use keyring.
                pass
        return True

    def migrate_legacy(self) -> bool:
        """Copy the pre-profile secret into this profile without exposing it."""
        if not self.profile_id or os.getenv("RISK_AGENT_API_KEY", "").strip() or self.read():
            return False
        legacy = SecretStore(self.paths)
        value = legacy.read()
        if not value:
            return False
        self.save(value)
        return True

    def _remove_local(self) -> None:
        try:
            self.local_path.unlink(missing_ok=True)
        except TypeError:  # Python 3.9 compatibility for local verification
            if self.local_path.exists():
                self.local_path.unlink()


def _safe_profile_id(value: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip()).strip(".-")
    if not candidate or len(candidate) > 80:
        raise ValueError("PROVIDER_PROFILE_ID_INVALID")
    return candidate
