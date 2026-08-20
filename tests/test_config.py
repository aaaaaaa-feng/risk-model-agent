from pathlib import Path

import app.config as config_module


def _isolate_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path / "projects")
    monkeypatch.setattr(config_module, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "app-config.json")


def test_clear_api_key_takes_priority_over_contradictory_replacement(
    monkeypatch, tmp_path: Path
) -> None:
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    cleared = []
    monkeypatch.setattr(config_module, "_clear_secret", lambda: cleared.append(True))
    monkeypatch.setattr(
        config_module,
        "_save_secret",
        lambda _api_key: (_ for _ in ()).throw(AssertionError("clear must take priority")),
    )

    saved = config_module.save_config({"api_key": "replacement", "clear_api_key": True})

    assert cleared == [True]
    assert saved["api_key_configured"] is False
    assert saved["secret_storage"] == "not_configured"
    assert saved["api_key"] == ""


def test_environment_api_key_cannot_be_reported_as_cleared(monkeypatch, tmp_path: Path) -> None:
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("RISK_AGENT_API_KEY", "environment-secret")
    monkeypatch.setattr(config_module, "_clear_secret", lambda: None)

    saved = config_module.save_config({"clear_api_key": True})
    loaded = config_module.load_config()

    assert saved["api_key_configured"] is True
    assert saved["secret_storage"] == "environment"
    assert saved["api_key"] == "••••••••"
    assert loaded["api_key_configured"] is True
    assert loaded["secret_storage"] == "environment"


def test_environment_api_key_prevents_unused_shadow_secret(monkeypatch, tmp_path: Path) -> None:
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("RISK_AGENT_API_KEY", "environment-secret")
    monkeypatch.setattr(
        config_module,
        "_save_secret",
        lambda _api_key: (_ for _ in ()).throw(AssertionError("environment key is authoritative")),
    )

    saved = config_module.save_config({"api_key": "unused-local-secret"})

    assert saved["api_key_configured"] is True
    assert saved["secret_storage"] == "environment"
    assert saved["api_key"] == "••••••••"
