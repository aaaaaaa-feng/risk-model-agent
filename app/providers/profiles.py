from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import PROVIDER_PRESETS, Settings
from app.core.paths import AppPaths, get_paths

from .secrets import SecretStore


PROFILE_SCHEMA = "risk-provider-profiles/v1"
PROFILE_FIELDS = ("provider", "api_format", "base_url", "model", "reviewer_model", "llm_enabled")


class ProviderProfileStore:
    """Persist non-secret Provider settings and namespace their API keys.

    The old single-key store remains readable through ``migrate_legacy``
    so an upgrade cannot make an existing configuration disappear.  Secrets
    themselves never enter this JSON file.
    """

    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_paths()

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.paths.provider_profiles.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        profiles = value.get("profiles")
        if not isinstance(profiles, list):
            profiles = []
        clean: list[dict[str, Any]] = []
        for item in profiles:
            if not isinstance(item, dict):
                continue
            profile_id = str(item.get("id") or "").strip()
            provider = str(item.get("provider") or "").strip()
            if not profile_id or not provider:
                continue
            clean.append({"id": profile_id, **{key: item.get(key) for key in PROFILE_FIELDS}})
        active = str(value.get("active_profile_id") or "").strip()
        return {"schema_version": PROFILE_SCHEMA, "active_profile_id": active, "profiles": clean}

    def ensure_for_settings(self, settings: Settings) -> str:
        data = self.load()
        profiles: list[dict[str, Any]] = data["profiles"]
        stored_active_id = str(data.get("active_profile_id") or "")
        active = next(
            (
                item
                for item in profiles
                if item.get("id") == stored_active_id and item.get("provider") == settings.provider
            ),
            None,
        )
        if active is None:
            active = next(
                (item for item in profiles if item.get("provider") == settings.provider),
                None,
            )
        if active is None:
            profile_id = _profile_id(settings.provider)
            existing_ids = {str(item["id"]) for item in profiles}
            if profile_id in existing_ids:
                profile_id = f"{profile_id}-{len(existing_ids) + 1}"
            active = {"id": profile_id, **_settings_values(settings)}
            profiles.append(active)
        else:
            profile_id = str(active["id"])
        data["active_profile_id"] = profile_id
        self._write(data)
        # Migrate the pre-v1 key exactly once to the active namespace.  This
        # makes an upgrade appear seamless while keeping other profiles empty.
        SecretStore(self.paths, profile_id=profile_id).migrate_legacy()
        return profile_id

    def upsert(self, profile_id: str, values: dict[str, Any]) -> dict[str, Any]:
        profile_id = _profile_id(profile_id)
        data = self.load()
        profiles: list[dict[str, Any]] = data["profiles"]
        item = next((row for row in profiles if row["id"] == profile_id), None)
        if item is None:
            item = {"id": profile_id}
            profiles.append(item)
        for key in PROFILE_FIELDS:
            if key in values:
                item[key] = values[key]
        item.setdefault("provider", profile_id)
        item.setdefault("api_format", "openai")
        item.setdefault("base_url", "")
        item.setdefault("model", "")
        item.setdefault("reviewer_model", "")
        item.setdefault("llm_enabled", True)
        data["active_profile_id"] = profile_id
        self._write(data)
        return dict(item)

    def activate(self, profile_id: str) -> dict[str, Any]:
        profile_id = _profile_id(profile_id)
        data = self.load()
        item = next((row for row in data["profiles"] if row["id"] == profile_id), None)
        if item is None:
            raise ValueError("PROVIDER_PROFILE_NOT_FOUND")
        data["active_profile_id"] = profile_id
        self._write(data)
        return dict(item)

    def active_profile_id(self, settings: Settings | None = None) -> str:
        data = self.load()
        active = str(data.get("active_profile_id") or "").strip()
        active_profile = next(
            (item for item in data["profiles"] if str(item["id"]) == active),
            None,
        )
        if active_profile is not None and (
            settings is None or active_profile.get("provider") == settings.provider
        ):
            return active
        if settings:
            return self.ensure_for_settings(settings)
        return ""

    def get(self, profile_id: str) -> dict[str, Any] | None:
        profile_id = _profile_id(profile_id)
        return next(
            (dict(item) for item in self.load()["profiles"] if item["id"] == profile_id), None
        )

    def public_profiles(self, active_profile_id: str | None = None) -> list[dict[str, Any]]:
        data = self.load()
        active = active_profile_id or str(data.get("active_profile_id") or "")
        result: list[dict[str, Any]] = []
        for item in data["profiles"]:
            profile_id = str(item["id"])
            secret = SecretStore(self.paths, profile_id=profile_id)
            configured = bool(secret.read())
            storage = (
                "environment"
                if os.getenv("RISK_AGENT_API_KEY", "").strip()
                else ("not_configured" if not configured else "local-protected-file")
            )
            provider = str(item.get("provider") or profile_id)
            preset = PROVIDER_PRESETS.get(provider, {})
            result.append(
                {
                    "id": profile_id,
                    "label": str(preset.get("label") or provider),
                    "provider": provider,
                    "api_format": item.get("api_format") or preset.get("api_format", "openai"),
                    "base_url": item.get("base_url") or preset.get("base_url", ""),
                    "model": item.get("model") or preset.get("model", ""),
                    "reviewer_model": item.get("reviewer_model") or "",
                    "llm_enabled": bool(item.get("llm_enabled", True)),
                    "api_key_configured": configured,
                    "api_key": "••••••••" if configured else "",
                    "secret_storage": storage,
                    "active": profile_id == active,
                }
            )
        return result

    def _write(self, value: dict[str, Any]) -> None:
        self.paths.provider_profiles.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        fd, name = tempfile.mkstemp(
            prefix="provider_profiles-", suffix=".tmp", dir=self.paths.provider_profiles.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            Path(name).replace(self.paths.provider_profiles)
        finally:
            try:
                Path(name).unlink(missing_ok=True)
            except TypeError:
                if Path(name).exists():
                    Path(name).unlink()


def _settings_values(settings: Settings) -> dict[str, Any]:
    return {key: getattr(settings, key) for key in PROFILE_FIELDS}


def _profile_id(value: str) -> str:
    candidate = str(value).strip()
    if (
        not candidate
        or len(candidate) > 80
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in candidate
        )
    ):
        raise ValueError("PROVIDER_PROFILE_ID_INVALID")
    return candidate
