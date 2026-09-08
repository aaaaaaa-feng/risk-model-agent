from __future__ import annotations

from app.core.config import SettingsStore
from app.services.run_readiness import preflight, compare_runs
from app.workers.demo import install_demo_project
from tests.conftest import wait_for_run


def test_preflight_never_claims_untested_provider_connected(context):
    SettingsStore(context.paths).save({"llm_enabled": False})
    demo = install_demo_project(context.catalog, rows=500)
    result = preflight(context, demo["project"]["id"], demo["target_tasks"][0]["id"])
    assert result["executable"]
    assert result["provider"]["connectivity"] == "not_tested"
    assert result["mode"] == "deterministic_product"
    insufficient = preflight(
        context, demo["project"]["id"], demo["target_tasks"][0]["id"], {"max_candidate_fits": 1}
    )
    assert not insufficient["executable"]
    SettingsStore(context.paths).save({"llm_enabled": True, "run_token_budget": 0})
    unavailable = preflight(context, demo["project"]["id"], demo["target_tasks"][0]["id"])
    assert not unavailable["executable"]
    assert any(item["code"] == "RUN_TOKEN_BUDGET_REQUIRED" for item in unavailable["blockers"])


def test_duplicate_request_returns_same_run_and_objective_conflict_is_rejected(context):
    import pytest

    SettingsStore(context.paths).save({"llm_enabled": False})
    demo = install_demo_project(context.catalog, rows=500, mode="semi_trusted")
    args = (demo["project"]["id"], demo["target_tasks"][0]["id"], "semi_trusted")
    first = context.engine.create_run(*args, request_key="same-request")
    second = context.engine.create_run(*args, request_key="same-request")
    assert first["id"] == second["id"]
    with pytest.raises(ValueError, match="REQUEST_KEY_CONFLICT"):
        context.engine.create_run(
            *args, request_key="same-request", objective={"target_value": 0.9}
        )
    wait_for_run(context, first["id"], {"awaiting_decision"}, 30)
    context.engine.cancel(first["id"])


def test_missing_snapshot_is_not_comparable(context):
    result = compare_runs(
        {"id": "a", "project_id": "p", "status": "failed"},
        {"id": "b", "project_id": "p", "status": "failed"},
        context.database,
    )
    assert not result["directly_comparable"]
    assert "objective_snapshot_missing" in result["reasons"]
