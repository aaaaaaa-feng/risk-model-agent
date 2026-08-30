from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .io import ResourcePlan
from .metrics import binary_metrics, calibration_table, lift_table, psi, score_monotonicity
from .model_adapters import MODEL_REGISTRY, available_models
from .scoring import probability_to_score


SUPPORTED_MODELS = set(MODEL_REGISTRY.identifiers)


def __getattr__(name: str) -> Any:
    """Lazily preserve the historical scorecard class import path."""

    if name == "ScorecardEstimator":
        from .model_builders import ScorecardEstimator

        return ScorecardEstimator
    raise AttributeError(name)


@dataclass
class ModelBundle:
    name: str
    algorithm: str
    estimator: Any
    features: list[str]
    calibration: str
    score_config: dict[str, float]
    metrics: dict[str, Any]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.estimator.predict_proba(frame[self.features])[:, 1], dtype=float)


def recommend_models(resource: ResourcePlan | None = None) -> list[str]:
    availability = available_models()
    result = ["dummy", "scorecard", "regularized_logistic"]
    if availability["xgboost"]:
        result.append("xgboost")
    else:
        result.append("extra_trees")
    if resource and resource.strategy == "in_memory" and availability["lightgbm"]:
        result.append("lightgbm")
    return result


def _candidate(
    name: str, frame: pd.DataFrame, features: Sequence[str], positive: int, negative: int
) -> Any:
    return MODEL_REGISTRY.build(name, frame, features, positive, negative)


def _calibrators(
    estimator: Any, rows: int, positives: int, algorithm: str
) -> list[tuple[str, Any]]:
    from sklearn.base import clone
    from sklearn.calibration import CalibratedClassifierCV

    candidates: list[tuple[str, Any]] = [("uncalibrated", clone(estimator))]
    # The deployable scorecard is a transparent JSON WOE + logistic rule set.
    # Wrapping it in cross-validated calibration would require serializing several
    # fold-specific scorecards and would no longer be a conventional scorecard.
    if algorithm == "scorecard":
        return candidates
    candidates.append(("platt", CalibratedClassifierCV(clone(estimator), method="sigmoid", cv=3)))
    if rows >= 500 and positives >= 50:
        candidates.append(
            ("isotonic", CalibratedClassifierCV(clone(estimator), method="isotonic", cv=3))
        )
    return candidates


def _safe_cv(y: pd.Series) -> Any:
    from sklearn.model_selection import StratifiedKFold

    minimum_class = int(y.value_counts().min())
    if minimum_class < 2:
        raise ValueError("TRAIN_CLASS_COUNT_TOO_SMALL")
    folds = max(2, min(5, minimum_class))
    return StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)


def _selection_score(metrics: dict[str, Any], monotonic: dict[str, Any]) -> float:
    auc = metrics.get("roc_auc") or 0
    ks = metrics.get("ks") or 0
    brier = metrics.get("brier") or 1
    penalty = 0 if monotonic.get("absolute") else 0.05 * (monotonic.get("violations") or 1)
    return float(auc + 0.55 * ks - 0.12 * brier - penalty)


def _search_space(name: str) -> list[dict[str, Any]]:
    """Small, deterministic search spaces kept intentionally bounded."""

    from sklearn.model_selection import ParameterGrid

    spaces: dict[str, dict[str, list[Any]]] = {
        "regularized_logistic": {"model__C": [0.1, 0.3, 1.0]},
        "random_forest": {"model__max_depth": [5, 8, 12], "model__min_samples_leaf": [10, 20]},
        "extra_trees": {"model__max_depth": [6, 9, 12], "model__min_samples_leaf": [10, 15]},
        "xgboost": {"model__max_depth": [3, 4], "model__learning_rate": [0.025, 0.05]},
        "lightgbm": {"model__num_leaves": [15, 20, 31], "model__learning_rate": [0.025, 0.05]},
        "catboost": {"model__depth": [4, 6], "model__learning_rate": [0.025, 0.05]},
    }
    return list(ParameterGrid(spaces.get(name, {})))


def _tune_candidate(
    name: str,
    base: Any,
    train: pd.DataFrame,
    target: str,
    features: Sequence[str],
    cv: Any,
    budget: int,
) -> tuple[Any, list[dict[str, Any]]]:
    from sklearn.base import clone
    from sklearn.model_selection import cross_val_score

    if budget <= 0 or name in {"dummy", "scorecard"}:
        return base, []
    candidates = _search_space(name)[: max(1, min(int(budget), 12))]
    trials: list[dict[str, Any]] = []
    best = base
    best_score = float("-inf")
    for index, parameters in enumerate(candidates, start=1):
        estimator = clone(base).set_params(**parameters)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            scores = cross_val_score(
                estimator,
                train[list(features)],
                train[target].astype(int),
                cv=cv,
                scoring="roc_auc",
                n_jobs=1,
            )
        mean_score = float(np.nanmean(scores))
        trials.append(
            {
                "trial": index,
                "parameters": parameters,
                "roc_auc": mean_score,
                "fold_scores": [float(value) for value in scores],
            }
        )
        if mean_score > best_score:
            best_score = mean_score
            best = estimator
    return best, trials


def _selected_parameters(estimator: Any) -> dict[str, Any]:
    try:
        values = estimator.get_params(deep=True)
    except (AttributeError, TypeError):
        return {}
    selected: dict[str, Any] = {}
    for key, value in values.items():
        if not key.startswith("model__") or isinstance(value, (dict, list, tuple, set)):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            selected[key] = value
    return selected


def _fit_one_candidate(
    name: str,
    base: Any,
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    features: Sequence[str],
    positive: int,
    negative: int,
    cv: Any,
    score_config: dict[str, float] | None,
    search_budget: int = 0,
) -> tuple[dict[str, Any], ModelBundle]:
    from sklearn.base import clone
    from sklearn.model_selection import cross_val_predict

    from .model_builders import feature_importance

    tuned_base, search_trials = _tune_candidate(
        name, base, train, target, features, cv, search_budget
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        oof_probability = cross_val_predict(
            clone(tuned_base),
            train[list(features)],
            train[target].astype(int),
            cv=cv,
            method="predict_proba",
            n_jobs=1,
        )[:, 1]
    oof_metrics = binary_metrics(train[target].to_numpy(dtype=int), oof_probability)
    calibration_candidates: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    for calibration_name, estimator in _calibrators(tuned_base, len(train), positive, name):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            estimator.fit(train[list(features)], train[target].astype(int))
        test_probability = estimator.predict_proba(test[list(features)])[:, 1]
        metrics = binary_metrics(test[target].to_numpy(dtype=int), test_probability)
        calibration_candidates.append({"method": calibration_name, "metrics": metrics})
        fitted[calibration_name] = estimator
    calibration_candidates.sort(
        key=lambda item: (
            item["metrics"].get("brier") if item["metrics"].get("brier") is not None else 99,
            item["metrics"].get("log_loss") if item["metrics"].get("log_loss") is not None else 99,
        )
    )
    selected_calibration = calibration_candidates[0]["method"]
    estimator = fitted[selected_calibration]
    partition_metrics: dict[str, Any] = {}
    partition_lift: dict[str, Any] = {}
    probabilities: dict[str, np.ndarray] = {}
    for partition_name, sample in (("train", train), ("test", test)):
        probability = estimator.predict_proba(sample[list(features)])[:, 1]
        probabilities[partition_name] = probability
        partition_metrics[partition_name] = binary_metrics(
            sample[target].to_numpy(dtype=int), probability
        )
        partition_lift[partition_name] = lift_table(sample[target].to_numpy(dtype=int), probability)
    monotonic = score_monotonicity(partition_lift["test"])
    stability = psi(probabilities["train"], probabilities["test"])
    selection_score = _selection_score(partition_metrics["test"], monotonic)
    candidate = {
        "candidate": name,
        "name": name,
        "status": "trained",
        "calibration": selected_calibration,
        "calibration_comparison": calibration_candidates,
        "oof_metrics": oof_metrics,
        "train_metrics": partition_metrics["train"],
        "test_metrics": partition_metrics["test"],
        "lift": partition_lift,
        "test_monotonicity": monotonic,
        "train_test_score_psi": stability,
        "selection_score": selection_score,
        "fit_scope": "train_cv_only",
        "selection_scope": "test",
        "resampling": "none",
        "class_balance": {
            "positive": positive,
            "negative": negative,
            "strategy": "class_weight_or_scale_pos_weight",
        },
        "feature_importance": feature_importance(estimator, features),
        "search": {
            "enabled": bool(search_budget > 0 and search_trials),
            "budget": max(0, int(search_budget)),
            "trials": search_trials,
            "selected_parameters": _selected_parameters(tuned_base),
        },
    }
    bundle = ModelBundle(
        name,
        name,
        estimator,
        list(features),
        selected_calibration,
        score_config or {},
        candidate,
    )
    return candidate, bundle


def train_candidates(
    frame: pd.DataFrame,
    target: str,
    features: Sequence[str],
    split: dict[str, Any],
    models: Sequence[str] | None = None,
    resource: ResourcePlan | None = None,
    score_config: dict[str, float] | None = None,
    search_budget: int = 0,
) -> tuple[dict[str, Any], dict[str, ModelBundle]]:
    selected_models = list(models or recommend_models(resource))
    availability = available_models()
    requested_models = list(dict.fromkeys(selected_models))
    runnable_models = [
        name
        for name in requested_models
        if name in SUPPORTED_MODELS and availability.get(name, False)
    ]
    if not runnable_models:
        raise ValueError("NO_AVAILABLE_MODELS")
    indices = {key: np.asarray(value, dtype=int) for key, value in split["indices"].items()}
    train = frame.iloc[indices["train"]]
    test = frame.iloc[indices["test"]]
    oot = frame.iloc[indices["oot"]] if len(indices["oot"]) else frame.iloc[[]]
    y_train = train[target].astype(int)
    positive = int(y_train.sum())
    negative = len(y_train) - positive
    cv = _safe_cv(y_train)
    candidates: list[dict[str, Any]] = [
        {
            "candidate": name,
            "name": name,
            "status": "failed",
            "error_code": (
                "MODEL_UNSUPPORTED"
                if name not in SUPPORTED_MODELS
                else "MODEL_DEPENDENCY_UNAVAILABLE"
            ),
            "calibration": None,
            "test_metrics": {},
            "test_monotonicity": {},
            "selection_score": None,
            "fit_scope": "not_started",
            "selection_scope": "not_started",
        }
        for name in requested_models
        if name not in runnable_models
    ]
    bundles: dict[str, ModelBundle] = {}
    for name in runnable_models:
        try:
            base = _candidate(name, train, features, positive, negative)
            candidate, bundle = _fit_one_candidate(
                name,
                base,
                train,
                test,
                target,
                features,
                positive,
                negative,
                cv,
                score_config,
                search_budget=max(0, min(int(search_budget), 12)),
            )
            candidates.append(candidate)
            bundles[name] = bundle
        except Exception as exc:
            candidates.append(
                {
                    "candidate": name,
                    "name": name,
                    "status": "failed",
                    "error_code": f"MODEL_FIT_FAILED_{type(exc).__name__.upper()}",
                    "calibration": None,
                    "test_metrics": {},
                    "test_monotonicity": {},
                    "selection_score": None,
                    "fit_scope": "train_cv_only",
                    "selection_scope": "test",
                }
            )
    successful = [item for item in candidates if item["status"] == "trained"]
    if not successful:
        raise ValueError("NO_SUCCESSFUL_MODELS")
    non_dummy = [item for item in successful if item["candidate"] != "dummy"] or successful
    champion = max(non_dummy, key=lambda item: item["selection_score"])
    champion_bundle = bundles[champion["candidate"]]
    if len(oot):
        oot_probability = champion_bundle.predict_proba(oot)
        champion["oot_metrics"] = binary_metrics(oot[target].to_numpy(dtype=int), oot_probability)
        champion["lift"]["oot"] = lift_table(oot[target].to_numpy(dtype=int), oot_probability)
        champion["oot_monotonicity"] = score_monotonicity(champion["lift"]["oot"])
        champion["test_oot_score_psi"] = psi(champion_bundle.predict_proba(test), oot_probability)
        champion["oot_calibration"] = calibration_table(
            oot[target].to_numpy(dtype=int), oot_probability
        )
    all_probability = champion_bundle.predict_proba(frame)
    score_result = probability_to_score(all_probability, **(score_config or {}))
    report = {
        "candidates": candidates,
        "champion": champion["candidate"],
        "champion_metrics": {
            "train": champion["train_metrics"],
            "test": champion["test_metrics"],
            "oot": champion.get("oot_metrics"),
        },
        "score": {
            "config": score_result["config"],
            "floor_rate": score_result["floor_rate"],
            "cap_rate": score_result["cap_rate"],
        },
        "oot_used_for_selection": False,
        "resource_plan": resource.as_dict() if resource else None,
        "search_budget": max(0, min(int(search_budget), 12)),
    }
    return report, bundles
