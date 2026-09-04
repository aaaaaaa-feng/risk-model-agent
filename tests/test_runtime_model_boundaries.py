from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.pipeline import PIPELINE_STEPS, partition_model_proposals
from app.orchestration.contracts import TOOL_NODES
from app.services.pipeline import RunPipeline
from app.workers.model_adapters import MODEL_ADAPTERS, MODEL_REGISTRY
from app.workers.modeling import ScorecardEstimator, train_candidates


EXPECTED_MODELS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}


def test_model_registry_keeps_full_algorithm_contract_and_scorecard_import_path():
    assert set(MODEL_ADAPTERS) == EXPECTED_MODELS
    assert set(MODEL_REGISTRY.availability()) == EXPECTED_MODELS
    assert all(
        spec.builder_path.startswith("app.workers.model_builders:")
        for spec in MODEL_ADAPTERS.values()
    )
    assert ScorecardEstimator.__module__ == "app.workers.modeling"


def test_pipeline_graph_and_tool_registry_share_one_step_contract(app_paths):
    pipeline = RunPipeline(paths=app_paths)

    assert TOOL_NODES is PIPELINE_STEPS
    registered = {item["name"] for item in pipeline.registry.manifest()["tools"]}
    assert registered == {step.tool_name for step in PIPELINE_STEPS}
    assert "generate_and_review_code" not in registered
    assert "code_review" not in {step.graph_node for step in PIPELINE_STEPS}
    assert len({step.graph_node for step in PIPELINE_STEPS}) == len(PIPELINE_STEPS)
    assert all(callable(getattr(pipeline, step.handler, None)) for step in PIPELINE_STEPS)


def test_llm_model_proposals_preserve_safe_rejection_evidence():
    accepted, rejected = partition_model_proposals(
        ["xgboost", "missing_model", "Bad Model", {"name": "catboost"}, "xgboost"],
        {"xgboost": True, "catboost": True},
    )

    assert accepted == ["xgboost"]
    assert rejected == ["missing_model", "invalid_model_identifier"]


def test_training_preserves_explicit_evidence_for_unavailable_candidates(monkeypatch):
    frame = pd.DataFrame(
        {
            "x1": np.linspace(-2, 2, 120),
            "x2": np.cos(np.linspace(0, 6, 120)),
            "Y": (np.arange(120) % 3 == 0).astype(int),
        }
    )
    monkeypatch.setattr(
        "app.workers.modeling.available_models",
        lambda: {
            **{name: True for name in EXPECTED_MODELS},
            "random_forest": False,
        },
    )
    result, _ = train_candidates(
        frame,
        "Y",
        ["x1", "x2"],
        {
            "indices": {
                "train": np.arange(0, 80),
                "test": np.arange(80, 100),
                "oot": np.arange(100, 120),
            }
        },
        models=["unknown_model", "random_forest", "regularized_logistic"],
    )

    by_name = {item["candidate"]: item for item in result["candidates"]}
    assert by_name["unknown_model"]["error_code"] == "MODEL_UNSUPPORTED"
    assert by_name["random_forest"]["error_code"] == "MODEL_DEPENDENCY_UNAVAILABLE"
    assert by_name["regularized_logistic"]["status"] == "trained"
