from __future__ import annotations

import os
from dataclasses import asdict, fields
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import PROVIDER_PRESETS, Settings, SettingsStore
from app.core.errors import normalize_error_code, public_error_message
from app.providers.gateway import ProviderGateway
from app.providers.profiles import ProviderProfileStore
from app.providers.secrets import SecretStore
from app.runtime import AppContext

from .dependencies import context


router = APIRouter(tags=["providers-and-settings"])


class SettingsPayload(BaseModel):
    profile_id: str | None = None
    provider: str | None = None
    api_format: str | None = None
    base_url: str | None = None
    model: str | None = None
    reviewer_model: str | None = None
    llm_enabled: bool | None = None
    mode: str | None = None
    run_token_budget: int | None = Field(default=None, ge=0)
    monthly_token_budget: int | None = Field(default=None, ge=0)
    proxy: str | None = None
    ca_cert: str | None = None
    notebook_network: bool | None = None
    telemetry: bool | None = None
    auto_update: bool | None = None
    memory_budget_mb: int | None = Field(default=None, ge=256, le=131072)
    max_parallel_models: int | None = Field(default=None, ge=1, le=16)
    default_models: list[str] | None = None
    api_key: str | None = None
    clear_api_key: bool = False


class ResetSettings(BaseModel):
    confirm: bool
    clear_api_key: bool = False


@router.get("/providers/presets")
def presets() -> dict[str, Any]:
    values = dict(PROVIDER_PRESETS)
    values["kimi_code"] = values["kimi-code"]
    return {"presets": values}


@router.get("/providers/settings")
def get_settings(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    store = SettingsStore(ctx.paths)
    settings = store.load()
    profiles = ProviderProfileStore(ctx.paths)
    active_profile_id = profiles.ensure_for_settings(settings)
    secret = SecretStore(ctx.paths, profile_id=active_profile_id)
    configured = bool(secret.read())
    if configured != settings.api_key_configured:
        storage = (
            "environment"
            if os.getenv("RISK_AGENT_API_KEY", "").strip()
            else (
                settings.secret_storage
                if settings.secret_storage != "not_configured"
                else "local-protected-file"
            )
        )
        settings = store.save_secret_state(configured, storage if configured else "not_configured")
    public = settings.public(ctx.paths)
    public["active_profile_id"] = active_profile_id
    public["profiles"] = profiles.public_profiles(active_profile_id)
    return {
        "settings": public,
        "profiles": public["profiles"],
        "active_profile_id": active_profile_id,
        "provider_status": ProviderGateway(settings=settings, paths=ctx.paths).status(),
    }


@router.put("/providers/settings")
def save_settings(payload: SettingsPayload, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    profile_id = str(data.pop("profile_id", "") or data.get("provider") or "").strip()
    key = str(data.pop("api_key", "") or "").strip()
    clear = bool(data.pop("clear_api_key", False))
    if "base_url" in data:
        _validate_url(str(data["base_url"]))
    if data.get("provider") not in {None, *PROVIDER_PRESETS.keys()}:
        raise ValueError("PROVIDER_INVALID")
    if data.get("api_format") not in {None, "openai", "anthropic"}:
        raise ValueError("API_FORMAT_INVALID")
    if data.get("mode") not in {None, "semi_trusted", "fully_trusted"}:
        raise ValueError("RUN_MODE_INVALID")
    store = SettingsStore(ctx.paths)
    profiles = ProviderProfileStore(ctx.paths)
    # Snapshot the previously active profile before a provider switch.  This
    # preserves an existing legacy/single-provider key instead of overwriting
    # it when the user adds a second configuration.
    previous = store.load()
    profiles.ensure_for_settings(previous)
    settings = store.save(data)
    profile_id = profile_id or settings.provider
    profile_values = {
        field.name: getattr(settings, field.name)
        for field in fields(Settings)
        if field.name
        in {"provider", "api_format", "base_url", "model", "reviewer_model", "llm_enabled"}
    }
    profile_values["provider"] = settings.provider
    profiles.upsert(profile_id, profile_values)
    profiles_data = profiles.load()
    active_profile = next(
        (item for item in profiles_data["profiles"] if item["id"] == profile_id), None
    )
    if active_profile and active_profile.get("provider") != settings.provider:
        # A custom profile id may be used for a Provider; the active Settings
        # remain authoritative for the selected profile.
        profiles.upsert(profile_id, {"provider": settings.provider})
    secrets = SecretStore(ctx.paths, profile_id=profile_id)
    if clear:
        storage = secrets.clear(include_legacy=True)
        configured = bool(secrets.read())
        settings = store.save_secret_state(configured, storage)
    elif key:
        storage = secrets.save(key)
        settings = store.save_secret_state(True, storage)
    else:
        configured = bool(secrets.read())
        settings = store.save_secret_state(
            configured,
            settings.secret_storage if configured else "not_configured",
        )
    active_profile_id = profiles.ensure_for_settings(settings)
    public = settings.public(ctx.paths)
    public["active_profile_id"] = active_profile_id
    public["profiles"] = profiles.public_profiles(active_profile_id)
    return {
        "settings": public,
        "profiles": public["profiles"],
        "active_profile_id": active_profile_id,
        "provider_status": ProviderGateway(settings=settings, paths=ctx.paths).status(),
    }


@router.post("/providers/profiles/{profile_id}/activate")
def activate_profile(profile_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    profiles = ProviderProfileStore(ctx.paths)
    profile = profiles.activate(profile_id)
    store = SettingsStore(ctx.paths)
    values = {
        key: profile[key]
        for key in ("provider", "api_format", "base_url", "model", "reviewer_model", "llm_enabled")
        if key in profile
    }
    settings = store.save(values)
    active_profile_id = profiles.active_profile_id(settings)
    secret = SecretStore(ctx.paths, profile_id=active_profile_id)
    configured = bool(secret.read())
    settings = store.save_secret_state(
        configured,
        "environment"
        if os.getenv("RISK_AGENT_API_KEY", "").strip()
        else (settings.secret_storage if configured else "not_configured"),
    )
    public = settings.public(ctx.paths)
    public["active_profile_id"] = active_profile_id
    public["profiles"] = profiles.public_profiles(active_profile_id)
    return {
        "settings": public,
        "profiles": public["profiles"],
        "active_profile_id": active_profile_id,
        "provider_status": ProviderGateway(settings=settings, paths=ctx.paths).status(),
    }


@router.post("/providers/test")
def test_provider(
    payload: SettingsPayload | None = None, ctx: AppContext = Depends(context)
) -> dict[str, Any]:
    store = SettingsStore(ctx.paths)
    current = asdict(store.load())
    api_key = ""
    profile_id = ""
    if payload:
        provided = payload.model_dump(exclude_none=True)
        profile_id = str(provided.pop("profile_id", "") or provided.get("provider") or "").strip()
        api_key = str(provided.pop("api_key", "") or "").strip()
        provided.pop("clear_api_key", None)
        if profile_id:
            stored_profile = ProviderProfileStore(ctx.paths).get(profile_id)
            if stored_profile:
                current.update(
                    {
                        key: stored_profile[key]
                        for key in (
                            "provider",
                            "api_format",
                            "base_url",
                            "model",
                            "reviewer_model",
                            "llm_enabled",
                        )
                        if key in stored_profile
                    }
                )
        current.update(provided)
    allowed = {item.name for item in fields(Settings)}
    settings = Settings(**{key: value for key, value in current.items() if key in allowed})
    settings.llm_enabled = True
    if settings.base_url:
        _validate_url(settings.base_url)
    gateway = ProviderGateway(
        settings=settings,
        api_key=api_key or None,
        paths=ctx.paths,
        profile_id=profile_id or None,
    )
    result = gateway.connectivity_check()
    error_code = (
        normalize_error_code(result.error_code, "PROVIDER_REQUEST_FAILED")
        if not result.ok
        else None
    )
    return {
        "ok": result.ok,
        "error_code": error_code,
        "error_message": public_error_message(error_code) if error_code else None,
        "model": result.model or settings.model,
        "endpoint": gateway.endpoint(),
        "api_format": gateway.api_format,
        "payload_hash": result.payload_hash,
    }


@router.get("/tools")
def tool_manifest(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return ctx.pipeline.registry.manifest()


@router.post("/system/reset-settings")
def reset_settings(payload: ResetSettings, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    if not payload.confirm:
        raise ValueError("SETTINGS_RESET_CONFIRMATION_REQUIRED")
    store = SettingsStore(ctx.paths)
    previous = store.load()
    profiles = ProviderProfileStore(ctx.paths)
    previous_profile_id = profiles.ensure_for_settings(previous)
    previous_secrets = SecretStore(ctx.paths, profile_id=previous_profile_id)
    if payload.clear_api_key:
        previous_secrets.clear(include_legacy=True)
    settings = store.reset(preserve_secret_state=True)
    active_profile_id = profiles.ensure_for_settings(settings)
    secrets = SecretStore(ctx.paths, profile_id=active_profile_id)
    configured = bool(secrets.read())
    storage = (
        "environment"
        if os.getenv("RISK_AGENT_API_KEY", "").strip()
        else ("local-protected-file" if configured else "not_configured")
    )
    settings = store.save_secret_state(configured, storage if configured else "not_configured")
    return {
        "settings": settings.public(ctx.paths),
        "api_key_cleared": payload.clear_api_key and not configured,
    }


def _validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise ValueError("PROVIDER_BASE_URL_MUST_BE_HTTPS_OR_LOCALHOST")
