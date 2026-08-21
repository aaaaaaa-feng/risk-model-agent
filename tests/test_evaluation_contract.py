from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings, SettingsStore
from app.evaluation.adapter import _provider_settings, run_eval_case
from app.evaluation.fakes import ScriptedProviderFactory
from app.evaluation.manifest import canonical_hash, compare_manifests, verify_manifest
from app.providers.gateway import ProviderGateway


def test_manifest_comparison_refuses_to_attribute_incomparable_runs():
    baseline = {
        "git_sha": "a" * 40,
        "agent_graph_version": "v1",
        "tool_manifest_hash": "tool-a",
        "policy_hash": "policy-a",
        "prompt_manifest": {"manifest_sha256": "prompt-a"},
        "dataset": {"content_sha256": "data-a"},
        "provider": {
            "name": "fake",
            "model": "model-a",
            "reviewer_model": "review-a",
            "parameters": {"temperature": 0},
        },
        "environment": {"python": "3.12", "os": "Darwin"},
        "eval_suite": {"suite_version": "suite/v1"},
    }
    same = json.loads(json.dumps(baseline))
    assert compare_manifests(baseline, same)["comparable"] is True
    same["dataset"]["content_sha256"] = "data-b"
    result = compare_manifests(baseline, same)
    assert result["comparable"] is False
    assert {item["field"] for item in result["differences"]} == {"dataset.content_sha256"}

    immutable = {**baseline}
    immutable["manifest_sha256"] = canonical_hash(immutable)
    assert verify_manifest(immutable, immutable["manifest_sha256"]) is immutable
    tampered = json.loads(json.dumps(immutable))
    tampered["provider"]["model"] = "tampered-model"
    with pytest.raises(ValueError, match="RUN_MANIFEST_INTEGRITY_FAILED"):
        verify_manifest(tampered, immutable["manifest_sha256"])


def test_provider_json_request_is_finalized_once_after_schema_validation(app_paths):
    requested: list[dict] = []
    completed: list[tuple[str, object]] = []

    def on_request(purpose, evidence, model):
        requested.append({"purpose": purpose, "evidence": evidence, "model": model})
        return "provider_test_001"

    gateway = ProviderGateway(
        Settings(
            provider="custom",
            base_url="https://eval.invalid/v1",
            model="fake-main-v1",
            llm_enabled=True,
        ),
        api_key="eval-key-not-real",
        client_factory=ScriptedProviderFactory(["provider_invalid_json"]),
        request_callback=on_request,
        result_callback=lambda identifier, result: completed.append((identifier, result)),
        paths=app_paths,
    )
    payload, result = gateway.complete_json("Return JSON.", {"safe": True})
    assert payload is None
    assert result.error_code == "PROVIDER_SCHEMA_INVALID"
    assert result.response_hash
    assert len(requested) == 1
    assert len(completed) == 1
    assert completed[0][0] == "provider_test_001"


def test_provider_http_failure_records_status_and_response_hash(app_paths):
    gateway = ProviderGateway(
        Settings(
            provider="custom",
            base_url="https://eval.invalid/v1",
            model="fake-main-v1",
            llm_enabled=True,
        ),
        api_key="eval-key-not-real",
        client_factory=ScriptedProviderFactory(["provider_auth_failed"]),
        paths=app_paths,
    )
    result = gateway.complete("Return JSON.", {"safe": True})
    assert result.ok is False
    assert result.error_code == "PROVIDER_AUTH_FAILED"
    assert result.http_status == 401
    assert result.response_hash


def test_provider_dlp_block_has_a_terminal_audit_record_without_sensitive_value(app_paths):
    requested: list[dict] = []
    completed: list[object] = []
    factory = ScriptedProviderFactory()

    def on_request(purpose, evidence, model):
        requested.append({"purpose": purpose, "evidence": evidence, "model": model})
        return "provider_dlp_001"

    gateway = ProviderGateway(
        Settings(
            provider="custom",
            base_url="https://eval.invalid/v1",
            model="fake-main-v1",
            llm_enabled=True,
        ),
        api_key="eval-key-not-real",
        client_factory=factory,
        request_callback=on_request,
        result_callback=lambda identifier, result: completed.append((identifier, result)),
        paths=app_paths,
    )
    result = gateway.complete("Return JSON.", {"summary": "13812345678"})
    assert result.error_code == "DLP_BLOCK"
    assert len(requested) == len(completed) == 1
    assert requested[0]["evidence"] == {
        "schema_version": "provider-blocked-request/v1",
        "blocked": True,
        "block_code": "DLP_BLOCK",
    }
    assert completed[0][0] == "provider_dlp_001"
    assert completed[0][1].error_code == "DLP_BLOCK"
    assert factory.calls == []


def test_configured_provider_secret_is_separated_and_invalid_trial_leaves_no_directory(
    app_paths, tmp_path: Path
):
    provider = _provider_settings(
        "configured_provider",
        {
            "provider": "custom",
            "api_format": "openai",
            "base_url": "https://provider.example/v1",
            "model": "candidate-model",
            "reviewer_model": "reviewer-model",
            "api_key": "ephemeral-eval-secret",
        },
    )
    SettingsStore(app_paths).save(provider["settings"])
    persisted = app_paths.config.read_text(encoding="utf-8")
    assert "ephemeral-eval-secret" not in persisted
    assert "api_key" not in provider["settings"]
    assert provider["api_key"] == "ephemeral-eval-secret"

    artifact_root = tmp_path / "eval-results"
    with pytest.raises(ValueError, match="EVAL_PROVIDER_CONFIG_INCOMPLETE"):
        run_eval_case(
            case={
                "case_id": "configured_provider_invalid_001",
                "provider_profile": "configured_provider",
                "rows": 500,
            },
            provider={"base_url": "https://provider.example/v1", "model": "candidate"},
            trial_id="trial_001",
            artifact_root=artifact_root,
        )
    assert not artifact_root.exists()
    with pytest.raises(ValueError, match="EVAL_PROVIDER_FAULT_REQUIRES_FAKE_PROVIDER"):
        run_eval_case(
            case={
                "case_id": "invalid_fault_profile_001",
                "provider_profile": "deterministic",
                "faults": ["provider_timeout"],
                "rows": 500,
            },
            trial_id="trial_001",
            artifact_root=artifact_root,
        )


def test_target_adapter_runs_semi_trusted_case_and_exports_safe_trace(tmp_path: Path):
    result = run_eval_case(
        case={
            "case_id": "core_split_001",
            "mode": "semi_trusted",
            "provider_profile": "deterministic",
            "expected_terminal_state": "succeeded",
            "rows": 500,
            "cleanup_workspace": True,
        },
        trial_id="trial_001",
        artifact_root=tmp_path / "eval-results",
    )
    assert result["expectation_met"] is True
    assert result["terminal_state"] == "succeeded"
    trace_path = Path(result["trace_bundle_path"])
    artifact_path = Path(result["artifact_manifest_path"])
    assert trace_path.is_file() and artifact_path.is_file()
    assert not (trace_path.parents[1] / "workspace").exists()
    bundle = json.loads(trace_path.read_text(encoding="utf-8"))
    assert bundle["manifest"]["eval_suite"]["case_id"] == "core_split_001"
    assert bundle["manifest"]["eval_suite"]["trial_id"] == "trial_001"
    assert bundle["raw_records_included"] is False
    assert bundle["hidden_chain_of_thought_included"] is False
    span_ids = {span["id"] for span in bundle["spans"]}
    root = bundle["trace"]["root_span_id"]
    assert root in span_ids
    assert all(span["id"] == root or span["parent_span_id"] in span_ids for span in bundle["spans"])
    assert {"tool", "gate", "reviewer"}.issubset({span["kind"] for span in bundle["spans"]})
    assert "stored_path" not in trace_path.read_text(encoding="utf-8")


def test_target_adapter_fault_injection_has_structured_failure(tmp_path: Path):
    result = run_eval_case(
        case={
            "case_id": "recovery_provider_timeout_001",
            "mode": "fully_trusted",
            "provider_profile": "fake_provider",
            "faults": ["provider_timeout", "worker_error:diagnose_data"],
            "expected_terminal_state": "failed",
            "rows": 500,
            "cleanup_workspace": True,
        },
        trial_id="trial_001",
        artifact_root=tmp_path / "eval-results",
    )
    assert result["expectation_met"] is True
    bundle = json.loads(Path(result["trace_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["run"]["error"] == "WORKER_ERROR_INJECTED"
    assert bundle["provider_requests"]
    assert {item["status"] for item in bundle["provider_requests"]} == {"failed"}
    assert any(item["status"] == "fallback_pass" for item in bundle["reviews"])
    assert any(
        span["status"] == "failed" and span["error_code"] == "WORKER_ERROR_INJECTED"
        for span in bundle["spans"]
    )


def test_reviewer_block_cannot_be_auto_approved_in_fully_trusted_mode(tmp_path: Path):
    result = run_eval_case(
        case={
            "case_id": "safety_reviewer_block_001",
            "mode": "fully_trusted",
            "provider_profile": "fake_provider",
            "faults": ["reviewer_block"],
            "expected_terminal_state": "blocked",
            "rows": 500,
        },
        trial_id="trial_001",
        artifact_root=tmp_path / "eval-results",
    )
    assert result["expectation_met"] is True
    bundle = json.loads(Path(result["trace_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["run"]["status"] == "blocked"
    assert bundle["run"]["error"] == "REVIEWER_BLOCKED"
    assert any(item["status"] == "blocked" for item in bundle["reviews"])
    assert any(
        item["status"] == "rejected" and item["review_status"] == "blocked"
        for item in bundle["decisions"]
    )
    assert not any(span["node"] == "diagnose" for span in bundle["spans"])


def test_reviewer_revision_cannot_be_auto_approved_in_fully_trusted_mode(tmp_path: Path):
    result = run_eval_case(
        case={
            "case_id": "safety_reviewer_revise_001",
            "mode": "fully_trusted",
            "provider_profile": "fake_provider",
            "faults": ["reviewer_revise"],
            "expected_terminal_state": "blocked",
            "rows": 500,
        },
        trial_id="trial_001",
        artifact_root=tmp_path / "eval-results",
    )
    assert result["expectation_met"] is True
    bundle = json.loads(Path(result["trace_bundle_path"]).read_text(encoding="utf-8"))
    assert bundle["run"]["error"] == "REVIEWER_REVISION_UNRESOLVED"
    assert any(item["status"] == "revise" for item in bundle["reviews"])
    assert any(
        item["status"] == "rejected" and item["review_status"] == "revise"
        for item in bundle["decisions"]
    )
