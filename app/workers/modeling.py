from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .binning import fit_binning, woe_transform
from .io import ResourcePlan
from .metrics import binary_metrics, calibration_table, lift_table, psi, score_monotonicity
from .scoring import probability_to_score


SUPPORTED_MODELS = {
    "dummy",
    "scorecard",
    "regularized_logistic",
    "random_forest",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
}


class ScorecardEstimator(ClassifierMixin, BaseEstimator):
    def __init__(self, max_iter: int = 800, class_weight: str | None = "balanced"):
        self.max_iter = max_iter
        self.class_weight = class_weight

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "ScorecardEstimator":
        frame = X.copy()
        frame["__target__"] = np.asarray(y, dtype=int)
        self.features_ = list(X.columns)
        self.binning_ = fit_binning(frame, "__target__", self.features_)
        transformed = woe_transform(X, self.binning_)
        self.model_ = LogisticRegression(
            max_iter=self.max_iter,
            class_weight=self.class_weight,
            solver="liblinear",
            random_state=42,
        )
        self.model_.fit(transformed, y)
        self.classes_ = self.model_.classes_
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model_.predict_proba(woe_transform(X[self.features_], self.binning_))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


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


def _feature_importance(estimator: Any, features: Sequence[str]) -> list[dict[str, Any]]:
    if isinstance(estimator, ScorecardEstimator):
        values = np.asarray(estimator.model_.coef_[0], dtype=float)
        names = list(estimator.features_)
    else:
        fitted = estimator
        if isinstance(estimator, CalibratedClassifierCV):
            calibrated = getattr(estimator, "calibrated_classifiers_", [])
            fitted = getattr(calibrated[0], "estimator", None) if calibrated else None
        if fitted is None or not hasattr(fitted, "named_steps"):
            return []
        preprocess = fitted.named_steps.get("preprocess")
        model = fitted.named_steps.get("model")
        if preprocess is None or model is None:
            return []
        try:
            names = [str(value) for value in preprocess.get_feature_names_out()]
        except (AttributeError, ValueError):
            names = list(features)
        if hasattr(model, "feature_importances_"):
            values = np.asarray(model.feature_importances_, dtype=float)
        elif hasattr(model, "coef_"):
            values = np.abs(np.asarray(model.coef_[0], dtype=float))
        else:
            return []
    rows = [
        {"feature": name, "importance": float(value)}
        for name, value in zip(names, values, strict=False)
    ]
    return sorted(rows, key=lambda item: item["importance"], reverse=True)


def _one_hot_encoder() -> OneHotEncoder:
    parameters = inspect.signature(OneHotEncoder).parameters
    kwargs: dict[str, Any] = {"handle_unknown": "ignore", "min_frequency": 0.005}
    if "sparse_output" in parameters:
        kwargs["sparse_output"] = True
    else:  # pragma: no cover - older sklearn
        kwargs["sparse"] = True
    return OneHotEncoder(**kwargs)


def _preprocessor(frame: pd.DataFrame, features: Sequence[str], scale: bool) -> ColumnTransformer:
    numeric = [column for column in features if pd.api.types.is_numeric_dtype(frame[column])]
    categorical = [column for column in features if column not in numeric]
    transformers: list[tuple[str, Any, list[str]]] = []
    numeric_steps: list[tuple[str, Any]] = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        numeric_steps.append(("scale", StandardScaler()))
    if numeric:
        transformers.append(("numeric", Pipeline(numeric_steps), numeric))
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("encode", _one_hot_encoder()),
                    ]
                ),
                categorical,
            )
        )
    return ColumnTransformer(transformers, remainder="drop", verbose_feature_names_out=False)


def available_models() -> dict[str, bool]:
    result = {name: True for name in SUPPORTED_MODELS}
    for name, module in (
        ("xgboost", "xgboost"),
        ("lightgbm", "lightgbm"),
        ("catboost", "catboost"),
    ):
        try:
            __import__(module)
        except ImportError:
            result[name] = False
    return result


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
    weight_ratio = negative / max(positive, 1)
    if name == "dummy":
        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                ("model", DummyClassifier(strategy="prior")),
            ]
        )
    if name == "scorecard":
        return ScorecardEstimator()
    if name == "regularized_logistic":
        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, True)),
                (
                    "model",
                    LogisticRegression(
                        C=0.3,
                        penalty="l2",
                        solver="liblinear",
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=42,
                    ),
                ),
            ]
        )
    if name == "random_forest":
        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=240,
                        max_depth=8,
                        min_samples_leaf=20,
                        class_weight="balanced_subsample",
                        random_state=42,
                        n_jobs=1,
                    ),
                ),
            ]
        )
    if name == "extra_trees":
        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                (
                    "model",
                    ExtraTreesClassifier(
                        n_estimators=240,
                        max_depth=9,
                        min_samples_leaf=15,
                        class_weight="balanced",
                        random_state=42,
                        n_jobs=1,
                    ),
                ),
            ]
        )
    if name == "xgboost":
        from xgboost import XGBClassifier

        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                (
                    "model",
                    XGBClassifier(
                        n_estimators=300,
                        max_depth=4,
                        learning_rate=0.035,
                        subsample=0.85,
                        colsample_bytree=0.8,
                        reg_lambda=3,
                        scale_pos_weight=weight_ratio,
                        eval_metric="logloss",
                        random_state=42,
                        n_jobs=1,
                    ),
                ),
            ]
        )
    if name == "lightgbm":
        from lightgbm import LGBMClassifier

        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                (
                    "model",
                    LGBMClassifier(
                        n_estimators=300,
                        num_leaves=20,
                        max_depth=6,
                        learning_rate=0.035,
                        class_weight="balanced",
                        random_state=42,
                        n_jobs=1,
                        verbosity=-1,
                    ),
                ),
            ]
        )
    if name == "catboost":
        from catboost import CatBoostClassifier

        return Pipeline(
            [
                ("preprocess", _preprocessor(frame, features, False)),
                (
                    "model",
                    CatBoostClassifier(
                        iterations=300,
                        depth=6,
                        learning_rate=0.035,
                        auto_class_weights="Balanced",
                        random_seed=42,
                        verbose=False,
                        thread_count=1,
                        allow_writing_files=False,
                    ),
                ),
            ]
        )
    raise ValueError(f"UNSUPPORTED_MODEL: {name}")


def _calibrators(
    estimator: Any, rows: int, positives: int, algorithm: str
) -> list[tuple[str, Any]]:
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


def _safe_cv(y: pd.Series) -> StratifiedKFold:
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


def _fit_one_candidate(
    name: str,
    base: Any,
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    features: Sequence[str],
    positive: int,
    negative: int,
    cv: StratifiedKFold,
    score_config: dict[str, float] | None,
) -> tuple[dict[str, Any], ModelBundle]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        oof_probability = cross_val_predict(
            clone(base),
            train[list(features)],
            train[target].astype(int),
            cv=cv,
            method="predict_proba",
            n_jobs=1,
        )[:, 1]
    oof_metrics = binary_metrics(train[target].to_numpy(dtype=int), oof_probability)
    calibration_candidates: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    for calibration_name, estimator in _calibrators(base, len(train), positive, name):
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
        "feature_importance": _feature_importance(estimator, features),
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
) -> tuple[dict[str, Any], dict[str, ModelBundle]]:
    selected_models = list(models or recommend_models(resource))
    availability = available_models()
    selected_models = [
        name
        for name in selected_models
        if name in SUPPORTED_MODELS and availability.get(name, True)
    ]
    if not selected_models:
        raise ValueError("NO_AVAILABLE_MODELS")
    indices = {key: np.asarray(value, dtype=int) for key, value in split["indices"].items()}
    train = frame.iloc[indices["train"]]
    test = frame.iloc[indices["test"]]
    oot = frame.iloc[indices["oot"]] if len(indices["oot"]) else frame.iloc[[]]
    y_train = train[target].astype(int)
    positive = int(y_train.sum())
    negative = len(y_train) - positive
    cv = _safe_cv(y_train)
    candidates: list[dict[str, Any]] = []
    bundles: dict[str, ModelBundle] = {}
    for name in selected_models:
        try:
            base = _candidate(name, train, features, positive, negative)
            candidate, bundle = _fit_one_candidate(
                name, base, train, test, target, features, positive, negative, cv, score_config
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
    }
    return report, bundles
