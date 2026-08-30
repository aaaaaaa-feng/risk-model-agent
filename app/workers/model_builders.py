from __future__ import annotations

import inspect
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .binning import fit_binning, woe_transform


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


# Existing model packages resolve this public class from app.workers.modeling.
# Keep that module identity while loading the implementation only when a model
# is actually constructed or deserialized.
ScorecardEstimator.__module__ = "app.workers.modeling"


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


def feature_importance(estimator: Any, features: Sequence[str]) -> list[dict[str, Any]]:
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


def build_dummy(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
    return Pipeline(
        [
            ("preprocess", _preprocessor(frame, features, False)),
            ("model", DummyClassifier(strategy="prior")),
        ]
    )


def build_scorecard(
    _frame: pd.DataFrame, _features: Sequence[str], _positive: int, _negative: int
) -> ScorecardEstimator:
    return ScorecardEstimator()


def build_regularized_logistic(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
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


def build_random_forest(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
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


def build_extra_trees(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
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


def build_xgboost(
    frame: pd.DataFrame, features: Sequence[str], positive: int, negative: int
) -> Pipeline:
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
                    scale_pos_weight=negative / max(positive, 1),
                    eval_metric="logloss",
                    random_state=42,
                    n_jobs=1,
                ),
            ),
        ]
    )


def build_lightgbm(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
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


def build_catboost(
    frame: pd.DataFrame, features: Sequence[str], _positive: int, _negative: int
) -> Pipeline:
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
