"""Bounded optimization policy. No code execution, data access or final holdout evidence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.governance.manifest import canonical_hash


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["risk-objective/v1"] = "risk-objective/v1"
    primary_metric: Literal["roc_auc"] = "roc_auc"
    target_value: float = Field(default=0.75, ge=0.5, le=1)
    min_ks: float = Field(default=0, ge=0, le=1)
    max_overfit_gap: float = Field(default=1, ge=0, le=1)
    max_psi: float = Field(default=100, ge=0, le=100)
    max_features: int = Field(default=1000, ge=1, le=1000)
    max_optimizations: int = Field(default=3, ge=0, le=3)
    max_candidate_fits: int = Field(default=300, ge=1, le=10000)
    max_seconds: int = Field(default=600, ge=1, le=3600)
    min_improvement: float = Field(default=0.001, gt=0, le=0.1)
    patience: int = Field(default=2, ge=1, le=3)
    strategy: Literal["fixed", "parameter_search", "agent"] = "agent"


# Deliberately small legal parameter domain; arbitrary sklearn parameters are forbidden.
PARAMETERS = {
    "regularized_logistic": {"model__C": [0.03, 0.1, 0.3, 1.0, 3.0]},
    "extra_trees": {"model__max_depth": [4, 6, 9, 12], "model__min_samples_leaf": [10, 15, 20]},
    "random_forest": {"model__max_depth": [4, 5, 8, 12], "model__min_samples_leaf": [10, 20]},
    "xgboost": {"model__max_depth": [2, 3, 4], "model__learning_rate": [0.025, 0.05]},
    "lightgbm": {"model__num_leaves": [7, 15, 20, 31], "model__learning_rate": [0.025, 0.05]},
    "catboost": {"model__depth": [3, 4, 6], "model__learning_rate": [0.025, 0.05]},
}


class PatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["risk-patch-plan/v1"] = "risk-patch-plan/v1"
    parent_hash: str
    diagnosis: Literal[
        "weak_signal", "overfit", "feature_quality", "model_constraint", "computation_failure"
    ]
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=1, max_length=500)
    expected_effect: str = Field(min_length=1, max_length=500)
    validation: Literal["frozen_development_protocol"] = "frozen_development_protocol"
    models: list[str] | None = None
    parameters: dict[str, dict[str, Any]] | None = None
    features: list[str] | None = None


def plan_hash(plan: dict) -> str:
    return canonical_hash(
        {k: plan.get(k) for k in ("models", "parameters", "features", "search_budget")}
    )


def validate_patch(
    value: dict,
    parent: dict,
    allowed_features: list[str],
    allowed_models: list[str],
    evidence_refs: list[str],
) -> tuple[dict, dict]:
    patch = PatchPlan.model_validate(value).model_dump()
    if patch["parent_hash"] != plan_hash(parent):
        raise ValueError("PATCH_PARENT_MISMATCH")
    if not set(patch["evidence_refs"]).issubset(evidence_refs):
        raise ValueError("PATCH_EVIDENCE_FORBIDDEN")
    result = deepcopy(parent)
    for key in ("models", "parameters", "features"):
        if patch[key] is not None:
            result[key] = patch[key]
    if not result["models"] or not set(result["models"]).issubset(allowed_models):
        raise ValueError("PATCH_MODEL_FORBIDDEN")
    if not result["features"] or not set(result["features"]).issubset(allowed_features):
        raise ValueError("PATCH_FEATURE_FORBIDDEN")
    for model, parameters in result.get("parameters", {}).items():
        if model not in result["models"]:
            raise ValueError("PATCH_PARAMETER_MODEL_MISMATCH")
        for key, value in parameters.items():
            if value not in PARAMETERS.get(model, {}).get(key, []):
                raise ValueError("PATCH_PARAMETER_FORBIDDEN")
    result["search_budget"] = 0  # Explicit patches execute exactly, without hidden retuning.
    if plan_hash(result) == plan_hash(parent):
        raise ValueError("PATCH_NO_CHANGE")
    return patch, result


def candidate_rank(candidate: dict, objective: dict, feature_count: int) -> tuple[bool, float]:
    metrics = candidate.get("test_metrics") or {}
    auc = float(metrics.get("roc_auc") or 0)
    gap = float((candidate.get("train_metrics") or {}).get("roc_auc") or 0) - auc
    feasible = (
        float(metrics.get("ks") or 0) >= objective["min_ks"]
        and gap <= objective["max_overfit_gap"]
        and float(candidate.get("train_test_score_psi") or 0) <= objective["max_psi"]
        and feature_count <= objective["max_features"]
    )
    return feasible, auc


def diagnostic_evidence(result: dict, round_id: int) -> dict:
    champion = next(c for c in result["candidates"] if c["candidate"] == result["champion"])
    return {
        f"rounds.{round_id}.validation": champion["test_metrics"],
        f"rounds.{round_id}.train": champion["train_metrics"],
        f"rounds.{round_id}.stability": champion.get("train_test_score_psi"),
        f"rounds.{round_id}.failures": [
            c.get("error_code") for c in result["candidates"] if c["status"] != "trained"
        ],
    }


def local_proposal(parent: dict, evidence: dict, attempt: int) -> dict:
    values = list(evidence.values())
    overfit = (values[1].get("roc_auc") or 0) - (values[0].get("roc_auc") or 0) > 0.1
    model = "regularized_logistic" if overfit else "extra_trees"
    parameters = (
        {"model__C": [0.1, 0.03, 0.3][attempt - 1]}
        if overfit
        else {"model__max_depth": [6, 9, 4][attempt - 1], "model__min_samples_leaf": 20}
    )
    return {
        "parent_hash": plan_hash(parent),
        "diagnosis": "overfit" if overfit else "weak_signal",
        "evidence_refs": list(evidence)[:2],
        "reason": "训练与验证差距偏大，限制复杂度"
        if overfit
        else "开发验证未达目标，尝试受限非线性候选",
        "expected_effect": "验证泛化是否改善；不保证指标提高",
        "models": [model],
        "parameters": {model: parameters},
    }
