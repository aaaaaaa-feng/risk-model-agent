from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.codegen import (
    extract_generated_spec,
    generate_reproducible_notebook_code,
    review_generated_code,
)
from app.agents.evidence import build_safe_evidence
from app.core.config import PROVIDER_PRESETS, Settings
from app.core.security import (
    decrypt_bytes,
    decrypt_file_payload,
    encrypt_bytes,
    encrypt_file_payload,
    suppress_small_groups,
    validate_provider_text,
    validate_safe_evidence,
)
from app.providers.gateway import ProviderGateway
from app.providers.secrets import SecretStore


def test_safe_evidence_schema_name_is_not_false_positive_secret():
    evidence, aliases = build_safe_evidence(
        {"rows": 100, "columns": 1, "columns_detail": [{"name": "income", "type": "numeric", "missing_rate": 0, "unique_count": 90, "pii": False}]},
        {"valid_count": 100, "positive_count": 20, "negative_count": 80, "bad_rate": 0.2, "invalid_count": 0, "missing_count": 0},
    )
    assert evidence["schema_version"] == "risk-safe-evidence/v1"
    assert aliases == {"income": "f_0001"}


@pytest.mark.parametrize("value", ["api_key=secret-value", "Bearer abcdefghijklmnop", "sk-abcdefghijklmnop"])
def test_provider_text_blocks_secrets(value: str):
    with pytest.raises(ValueError):
        validate_provider_text(value)


def test_dlp_blocks_raw_rows_and_pii_values():
    with pytest.raises(ValueError, match="RAW_OR_PII_FIELD_FORBIDDEN"):
        validate_safe_evidence({"raw_rows": [{"x": 1}]})
    with pytest.raises(ValueError, match="POSSIBLE_PII_VALUE_FORBIDDEN"):
        validate_provider_text("手机号 13812345678")


def test_small_cells_are_suppressed_at_30():
    rows = suppress_small_groups([{"group": "a", "count": 29, "rate": 0.9}, {"group": "b", "count": 30, "rate": 0.2}])
    assert rows[0] == {"count": 29, "suppressed": True}
    assert rows[1]["rate"] == 0.2


def test_aes_gcm_bytes_and_streaming_recovery_key(tmp_path: Path):
    encrypted = encrypt_bytes(b"local-only", "correct horse battery")
    assert decrypt_bytes(encrypted, "correct horse battery") == b"local-only"
    source = tmp_path / "source.bin"
    source.write_bytes(b"risk" * 50_000)
    manifest, recovery = encrypt_file_payload(source, tmp_path / "encrypted.bin", "correct horse battery")
    restored = decrypt_file_payload(tmp_path / "encrypted.bin", tmp_path / "restored.bin", manifest, recovery)
    assert restored.read_bytes() == source.read_bytes()
    assert manifest["cipher"] == "AES-256-GCM"


def test_secret_store_is_scoped_to_application_paths(app_paths, monkeypatch):
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    store = SecretStore(app_paths)
    storage = store.save("local-test-key-value")
    assert storage in {"os-keychain", "local-protected-file"}
    assert store.read() == "local-test-key-value"
    assert store.clear() == "not_configured"
    assert store.read() == ""


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": json.dumps({"status": "ok"})}}], "usage": {"total_tokens": 2}}


class FakeClient:
    requests: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, endpoint, **kwargs):
        self.requests.append({"endpoint": endpoint, **kwargs})
        return FakeResponse()


def test_provider_presets_and_connectivity_without_raw_data(app_paths):
    assert {"deepseek", "kimi", "kimi-code", "openai", "anthropic"}.issubset(PROVIDER_PRESETS)
    settings = Settings(llm_enabled=True, provider="deepseek", model="deepseek-v4-flash")
    gateway = ProviderGateway(settings, api_key="test-key-not-real", client_factory=FakeClient, paths=app_paths)
    result = gateway.connectivity_check()
    assert result.ok is True
    request = FakeClient.requests[-1]
    assert request["endpoint"] == "https://api.deepseek.com/chat/completions"
    serialized = json.dumps(request["json"], ensure_ascii=False)
    assert "health_check" in serialized
    assert "raw_rows" not in serialized


def test_openai_and_anthropic_payload_contracts(app_paths):
    openai = ProviderGateway(Settings(provider="openai", model="gpt-5"), api_key="x", paths=app_paths)
    openai_body = openai._body("system", {"safe": True}, "gpt-5", 64)
    assert "max_completion_tokens" in openai_body and "temperature" not in openai_body
    anthropic = ProviderGateway(
        Settings(provider="anthropic", api_format="anthropic", base_url="https://api.anthropic.com", model="claude-sonnet-4-5"),
        api_key="x",
        paths=app_paths,
    )
    assert anthropic.endpoint() == "https://api.anthropic.com/v1/messages"
    assert anthropic._body("system", {"safe": True}, "claude-sonnet-4-5", 64)["system"] == "system"


def test_generated_code_has_immutable_spec_and_import_allowlist():
    source = generate_reproducible_notebook_code(
        dataset_file="/local/project/dataset.csv",
        target="Y",
        features=["income"],
        split={
            "method": "random_stratified",
            "time_column": None,
            "customer_key": "customer_id",
            "random_state": 42,
        },
        models=["dummy", "regularized_logistic"],
        score_config={"minimum": 300, "maximum": 900},
    )
    assert extract_generated_spec(source)["models"] == ["dummy", "regularized_logistic"]
    assert review_generated_code(source)["verdict"] == "pass"
    unsafe = source.replace("import pandas as pd", "import os\nimport pandas as pd")
    assert review_generated_code(unsafe)["verdict"] == "block"
