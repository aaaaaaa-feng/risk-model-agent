from __future__ import annotations

from copy import deepcopy

import pytest

from app.domain.optimization import (
    Objective,
    candidate_rank,
    local_proposal,
    plan_hash,
    validate_patch,
)
from app.workers.demo import install_demo_project
from app.core.config import SettingsStore
from tests.conftest import wait_for_run


def test_patch_rejects_oot_leakage_code_and_unchanged_plan():
    plan = {
        "models": ["regularized_logistic"],
        "features": ["safe"],
        "parameters": {},
        "search_budget": 0,
    }
    evidence = {"rounds.0.validation": {"roc_auc": 0.6}, "rounds.0.train": {"roc_auc": 0.9}}
    patch = local_proposal(plan, evidence, 1)
    valid, updated = validate_patch(patch, plan, ["safe"], ["regularized_logistic"], list(evidence))
    assert updated["parameters"]["regularized_logistic"]["model__C"] == 0.1
    assert plan_hash(updated) != plan_hash(plan)
    for malicious in (
        {"features": ["FPD7"]},
        {"evidence_refs": ["oot.roc_auc"]},
        {"code": "exec()"},
        {"parameters": {"regularized_logistic": {"model__n_jobs": 999}}},
    ):
        with pytest.raises(ValueError):
            validate_patch(
                {**patch, **malicious}, plan, ["safe"], ["regularized_logistic"], list(evidence)
            )
    unchanged = {**patch, "parent_hash": plan_hash(updated)}
    with pytest.raises(ValueError, match="PATCH_NO_CHANGE"):
        validate_patch(unchanged, updated, ["safe"], ["regularized_logistic"], list(evidence))


def test_feasibility_precedes_auc_and_objective_is_strict():
    objective = Objective(max_overfit_gap=0.1).model_dump()
    candidate = {"test_metrics": {"roc_auc": 0.8, "ks": 0.3}, "train_metrics": {"roc_auc": 0.99}}
    assert candidate_rank(candidate, objective, 2) == (False, 0.8)
    with pytest.raises(ValueError):
        Objective(max_optimizations=4)
    with pytest.raises(ValueError):
        Objective(oot_target=0.9)


def test_real_worker_optimizes_and_preserves_global_best(context):
    SettingsStore(context.paths).save(
        {"llm_enabled": False, "default_models": ["regularized_logistic"]}
    )
    demo = install_demo_project(context.catalog, mode="fully_trusted", rows=500)
    created = context.engine.create_run(
        demo["project"]["id"],
        demo["target_tasks"][0]["id"],
        "fully_trusted",
        objective={"target_value": 1.0},
    )
    run = wait_for_run(context, created["id"], {"succeeded", "failed", "blocked"}, 240)
    assert run["status"] == "succeeded", run.get("error")
    state = run["state"]
    rounds = state["optimization_rounds"]
    assert 2 <= len(rounds) <= 4
    assert len({r["plan_hash"] for r in rounds}) == len(rounds)
    assert all(r["result"]["champion_metrics"]["oot"] is None for r in rounds)
    assert state["model_result"]["final_holdout_evaluated"]
    best = rounds[state["best_round_id"]]
    assert (
        state["model_result"]["champion_metrics"]["test"]
        == best["result"]["champion_metrics"]["test"]
    )
    assert (
        state["candidate_fits_used"]
        <= state["objective_snapshot"]["objective"]["max_candidate_fits"]
    )
    assert state["goal_status"] == "unmet"
    assert all(r["approval"]["approved"] for r in rounds)
    assert state["optimization_stop_reason"] in {
        "no_improvement",
        "optimization_budget_exhausted",
        "repeated_plan",
    }


def test_second_worse_round_does_not_overwrite_best(context, monkeypatch):
    # Deterministic boundary test; synthetic metrics are not model-effect evidence.
    from app.services import pipeline as module
    from app.workers.modeling import ModelBundle
    from app.workers.io import plan_resources
    from app.core.database import new_id
    import pandas as pd

    pipeline = context.pipeline
    SettingsStore(context.paths).save({"llm_enabled": False})
    frame = pd.DataFrame({"x": [1, 2] * 30, "Y": [0, 1] * 30})
    monkeypatch.setattr(pipeline, "_working_frame", lambda state: frame)
    monkeypatch.setattr(pipeline, "_persist_bundles", lambda *args: "manifest")
    monkeypatch.setattr(pipeline, "_record_review", lambda *args: None)

    class Reviewer:
        def review_execution(self, result):
            return {"status": "deterministic_pass"}

        def llm_review(self, *args):
            return {}

        def combine(self, scope, deterministic, llm):
            return deterministic

    monkeypatch.setattr(pipeline, "_reviewer", lambda _: Reviewer())

    def train(*args, **kwargs):
        c = {
            "candidate": "dummy",
            "status": "trained",
            "train_metrics": {"roc_auc": 0.6},
            "test_metrics": {"roc_auc": 0.6, "ks": 0.2},
            "fit_count": 6,
        }
        return {"candidates": [c], "champion": "dummy"}, {
            "dummy": ModelBundle("dummy", "dummy", None, ["x"], "none", {}, c)
        }

    monkeypatch.setattr(module, "train_candidates", train)
    state = {
        "target": "Y",
        "active_plan": {
            "models": ["dummy"],
            "features": ["x"],
            "parameters": {},
            "search_budget": 0,
        },
        "model_plan": {"resource_plan": plan_resources(60, 2, 1536).as_dict(), "score": {}},
        "split": {},
        "objective_snapshot": {"objective": Objective().model_dump()},
        "optimization_deadline": 10**12,
        "optimization_rounds": [{"rank": [True, 0.8]}],
        "best_rank": [True, 0.8],
        "profile": {},
        "target_evidence": {},
        "screening": {},
    }
    original = deepcopy(state)
    update = pipeline.train_and_review(new_id("run"), state)
    assert "model_result" not in update
    assert not update["optimization_rounds"][-1]["replaced_best"]
    assert state == original


def test_optimization_human_refusal_never_runs_next_round(context):
    SettingsStore(context.paths).save(
        {"llm_enabled": False, "default_models": ["regularized_logistic"]}
    )
    demo = install_demo_project(context.catalog, mode="semi_trusted", rows=500)
    created = context.engine.create_run(
        demo["project"]["id"],
        demo["target_tasks"][0]["id"],
        "semi_trusted",
        objective={"target_value": 1.0},
    )
    run_id = created["id"]
    for _ in range(8):
        run = wait_for_run(context, run_id, {"awaiting_decision", "failed", "blocked"}, 120)
        assert run["status"] == "awaiting_decision", run.get("error")
        decision = next(
            d
            for d in context.database.list("decisions", {"run_id": run_id}, limit=100)
            if d["status"] == "pending"
        )
        reject = decision["stage"] == "optimization"
        context.engine.resume(run_id, decision["id"], not reject, {})
        if reject:
            final = wait_for_run(context, run_id, {"blocked", "failed"}, 30)
            assert final["status"] == "blocked"
            assert len(final["state"]["optimization_rounds"]) == 1
            assert not final["state"].get("model_version_id")
            return
        # Avoid reading the just-submitted pending state before the graph advances.
        import time

        time.sleep(0.1)
    pytest.fail("optimization gate not reached")


def test_cancel_pending_run_preserves_terminal_state(context):
    SettingsStore(context.paths).save({"llm_enabled": False})
    demo = install_demo_project(context.catalog, mode="semi_trusted", rows=500)
    created = context.engine.create_run(
        demo["project"]["id"], demo["target_tasks"][0]["id"], "semi_trusted"
    )
    wait_for_run(context, created["id"], {"awaiting_decision"}, 30)
    run = context.engine.cancel(created["id"])
    assert run["status"] == "blocked" and run["error"] == "RUN_CANCELLED"
    context.engine._mark_failure(run["id"], RuntimeError("late worker failure"))
    assert context.catalog.require("runs", run["id"])["error"] == "RUN_CANCELLED"
    assert context.engine.cancel(run["id"])["status"] == "blocked"
