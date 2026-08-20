from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import PROVIDER_PRESETS, Settings, SettingsStore
from app.providers.gateway import ProviderGateway
from app.providers.secrets import SecretStore
from app.runtime import AppContext

from .dependencies import context


router = APIRouter(tags=["providers-and-settings"])


class SettingsPayload(BaseModel):
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
    secret = SecretStore(ctx.paths)
    configured = bool(secret.read())
    if configured != settings.api_key_configured:
        storage = "environment" if __import__("os").getenv("RISK_AGENT_API_KEY", "").strip() else (settings.secret_storage if settings.secret_storage != "not_configured" else "local-or-keychain")
        settings = store.save_secret_state(configured, storage if configured else "not_configured")
    return {"settings": settings.public(ctx.paths), "provider_status": ProviderGateway(settings=settings, paths=ctx.paths).status()}


@router.put("/providers/settings")
def save_settings(payload: SettingsPayload, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    key = str(data.pop("api_key", "") or "").strip()
    clear = bool(data.pop("clear_api_key", False))
    if "base_url" in data:
        _validate_url(str(data["base_url"]))
    if data.get("api_format") not in {None, "openai", "anthropic"}:
        raise ValueError("API_FORMAT_INVALID")
    if data.get("mode") not in {None, "semi_trusted", "fully_trusted"}:
        raise ValueError("RUN_MODE_INVALID")
    store = SettingsStore(ctx.paths)
    settings = store.save(data)
    secrets = SecretStore(ctx.paths)
    if clear:
        storage = secrets.clear()
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
    return {"settings": settings.public(ctx.paths), "provider_status": ProviderGateway(settings=settings, paths=ctx.paths).status()}


@router.post("/providers/test")
def test_provider(payload: SettingsPayload | None = None, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    store = SettingsStore(ctx.paths)
    current = asdict(store.load())
    api_key = ""
    if payload:
        provided = payload.model_dump(exclude_none=True)
        api_key = str(provided.pop("api_key", "") or "").strip()
        provided.pop("clear_api_key", None)
        current.update(provided)
    allowed = {item.name for item in fields(Settings)}
    settings = Settings(**{key: value for key, value in current.items() if key in allowed})
    settings.llm_enabled = True
    if settings.base_url:
        _validate_url(settings.base_url)
    gateway = ProviderGateway(settings=settings, api_key=api_key, paths=ctx.paths)
    result = gateway.connectivity_check()
    return {
        "ok": result.ok,
        "error_code": result.error_code,
        "error_message": result.error_message,
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
    secrets = SecretStore(ctx.paths)
    previous = SettingsStore(ctx.paths).load()
    storage = previous.secret_storage
    configured = bool(secrets.read())
    if payload.clear_api_key:
        storage = secrets.clear()
        configured = bool(secrets.read())
    settings = SettingsStore(ctx.paths).reset(preserve_secret_state=True)
    settings = SettingsStore(ctx.paths).save_secret_state(configured, storage if configured else "not_configured")
    return {"settings": settings.public(ctx.paths), "api_key_cleared": payload.clear_api_key and not configured}


def _validate_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise ValueError("PROVIDER_BASE_URL_MUST_BE_HTTPS_OR_LOCALHOST")
