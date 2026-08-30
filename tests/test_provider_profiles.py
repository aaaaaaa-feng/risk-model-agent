from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.providers import secrets as secrets_module
from app.core.config import SettingsStore
from app.providers.gateway import ProviderGateway, ProviderResult


def test_provider_profiles_persist_switch_and_keep_keys_out_of_json(app_paths, monkeypatch):
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    monkeypatch.setattr(secrets_module, "_keyring", lambda: None)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        first = client.put(
            "/api/v1/providers/settings",
            json={"provider": "deepseek", "api_key": "deepseek-test-key", "llm_enabled": True},
        )
        assert first.status_code == 200
        second = client.put(
            "/api/v1/providers/settings",
            json={"provider": "openai", "api_key": "openai-test-key", "llm_enabled": True},
        )
        assert second.status_code == 200

        listed = client.get("/api/v1/providers/settings").json()
        assert listed["active_profile_id"] == "openai"
        profiles = {item["provider"]: item for item in listed["profiles"]}
        assert profiles["deepseek"]["api_key_configured"] is True
        assert profiles["openai"]["api_key_configured"] is True
        assert all("test-key" not in json.dumps(item) for item in profiles.values())

        activated = client.post("/api/v1/providers/profiles/deepseek/activate")
        assert activated.status_code == 200
        assert activated.json()["settings"]["provider"] == "deepseek"
        assert activated.json()["active_profile_id"] == "deepseek"

    saved_profiles = json.loads(app_paths.provider_profiles.read_text(encoding="utf-8"))
    serialized = json.dumps(saved_profiles, ensure_ascii=False)
    assert "deepseek-test-key" not in serialized
    assert "openai-test-key" not in serialized


def test_legacy_false_default_is_migrated_only_until_explicitly_disabled(app_paths):
    app_paths.config.write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "api_key_configured": True,
                "secret_storage": "local-protected-file",
                "llm_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    from app.core.config import SettingsStore

    store = SettingsStore(app_paths)
    assert store.load().llm_enabled is True
    store.save({"llm_enabled": False})
    assert store.load().llm_enabled is False


def test_gateway_reads_secret_from_the_active_custom_profile(app_paths, monkeypatch):
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    monkeypatch.setattr(secrets_module, "_keyring", lambda: None)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        client.put(
            "/api/v1/providers/settings",
            json={
                "profile_id": "deepseek-work",
                "provider": "deepseek",
                "api_key": "work-key",
                "llm_enabled": True,
            },
        )
        client.put(
            "/api/v1/providers/settings",
            json={
                "profile_id": "deepseek-personal",
                "provider": "deepseek",
                "api_key": "personal-key",
                "llm_enabled": True,
            },
        )
        listed = client.get("/api/v1/providers/settings").json()
        assert listed["active_profile_id"] == "deepseek-personal"
        assert (
            ProviderGateway(SettingsStore(app_paths).load(), paths=app_paths).key == "personal-key"
        )

        activated = client.post("/api/v1/providers/profiles/deepseek-work/activate").json()
        assert activated["active_profile_id"] == "deepseek-work"
        assert ProviderGateway(SettingsStore(app_paths).load(), paths=app_paths).key == "work-key"


def test_connectivity_check_uses_requested_profile_and_its_saved_key(app_paths, monkeypatch):
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    monkeypatch.setattr(secrets_module, "_keyring", lambda: None)
    observed = {}

    def connectivity_check(gateway):
        observed.update(
            {
                "provider": gateway.settings.provider,
                "model": gateway.settings.model,
                "key": gateway.key,
            }
        )
        return ProviderResult(ok=True, model=gateway.settings.model)

    monkeypatch.setattr(ProviderGateway, "connectivity_check", connectivity_check)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        client.put(
            "/api/v1/providers/settings",
            json={
                "profile_id": "deepseek-work",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key": "work-key",
            },
        )
        client.put(
            "/api/v1/providers/settings",
            json={
                "profile_id": "openai-personal",
                "provider": "openai",
                "model": "gpt-5-mini",
                "api_key": "personal-key",
            },
        )
        assert client.get("/api/v1/providers/settings").json()["active_profile_id"] == (
            "openai-personal"
        )

        checked = client.post(
            "/api/v1/providers/test",
            json={"profile_id": "deepseek-work"},
        )
        assert checked.status_code == 200
        assert checked.json()["ok"] is True
        assert observed == {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "key": "work-key",
        }
        assert client.get("/api/v1/providers/settings").json()["active_profile_id"] == (
            "openai-personal"
        )
