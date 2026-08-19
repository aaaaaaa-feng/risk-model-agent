"""Leakage-conscious candidate training for binary risk models."""

import hashlib
import json
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
import sklearn
from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import (
    StratifiedKFold,
    TimeSeriesSplit,
    train_test_split,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .metrics import (
    best_ks_threshold,
    evaluate_probabilities,
    lift_table,
)

SUPPORTED_CANDIDATES = (
    "dummy",
    "logistic_regression",
    "random_forest",
)

CANDIDATE_DISPLAY_NAMES = {
    "dummy": "Dummy baseline",
    "logistic_regression": "Logistic regression",
    "random_forest": "Random forest",
}


class _InputNormalizer(BaseEstimator, TransformerMixin):
    """Keep deterministic CSV type normalisation inside the saved pipeline."""

    def __init__(self, included_columns: Sequence[str], column_types: Dict[str, str]) -> None:
        self.included_columns = included_columns
        self.column_types = column_types

    def fit(self, features: pd.DataFrame, target: Any = None) -> "_InputNormalizer":
        _normalise_feature_values(features, self.included_columns, self.column_types)
        self.feature_names_in_ = np.asarray(self.included_columns, dtype=object)
        return self

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        return _normalise_feature_values(features, self.included_columns, self.column_types)

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.asarray(self.included_columns, dtype=object)


def _require_mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_probability(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not np.isfinite(result) or result <= 0.0 or result >= 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1")
    return result


def _normalise_plan(data_frame: pd.DataFrame, plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data_frame, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if data_frame.empty:
        raise ValueError("df must contain at least one row")
    if not isinstance(plan, dict):
        raise TypeError("plan must be a dictionary")

    target = _require_mapping(plan.get("target"), "target")
    features = _require_mapping(plan.get("features"), "features")
    split = _require_mapping(plan.get("split"), "split")

    target_column = target.get("column")
    if not isinstance(target_column, str) or not target_column:
        raise ValueError("target.column must be a non-empty string")
    if target_column not in data_frame.columns:
        raise ValueError(f"target column {target_column!r} is not present in df")
    if "positive_label" not in target:
        raise ValueError("target.positive_label is required")

    included = features.get("included_columns")
    if not isinstance(included, list) or not included:
        raise ValueError("features.included_columns must be a non-empty list")
    if not all(isinstance(column, str) and column for column in included):
        raise ValueError("every included feature must be a non-empty string")
    if len(set(included)) != len(included):
        raise ValueError("features.included_columns must not contain duplicates")
    if target_column in included:
        raise ValueError("the target column cannot also be an included feature")
    missing_columns = [column for column in included if column not in data_frame.columns]
    if missing_columns:
        raise ValueError(
            "included feature columns are missing: {}".format(", ".join(sorted(missing_columns)))
        )

    column_types = features.get("column_types", {})
    if not isinstance(column_types, dict):
        raise ValueError("features.column_types must be an object")

    resolved_types: Dict[str, str] = {}
    for column in included:
        declared = column_types.get(column)
        if declared is None:
            declared = (
                "numeric"
                if pd.api.types.is_numeric_dtype(data_frame[column])
                and not pd.api.types.is_bool_dtype(data_frame[column])
                else "categorical"
            )
        if declared in ("boolean", "text"):
            declared = "categorical"
        if declared not in ("numeric", "categorical"):
            raise ValueError(
                f"column type for {column!r} must be numeric, categorical, boolean, or text"
            )
        resolved_types[column] = declared

    split_method = split.get("method", "stratified_random")
    if split_method not in ("stratified_random", "time_holdout"):
        raise ValueError("split.method must be 'stratified_random' or 'time_holdout'")
    test_size = _require_probability(split.get("test_size", 0.2), "split.test_size")
    random_state = split.get("random_state", 42)
    if isinstance(random_state, bool) or not isinstance(random_state, (int, np.integer)):
        raise ValueError("split.random_state must be an integer")

    time_column = split.get("time_column")
    if split_method == "time_holdout":
        if not isinstance(time_column, str) or not time_column:
            raise ValueError("split.time_column is required for time_holdout")
        if time_column not in data_frame.columns:
            raise ValueError(f"time column {time_column!r} is not present in df")
    else:
        time_column = None

    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    if len(set(candidates)) != len(candidates):
        raise ValueError("candidates must not contain duplicates")
    unsupported = [name for name in candidates if name not in SUPPORTED_CANDIDATES]
    if unsupported:
        raise ValueError("unsupported candidates: {}".format(", ".join(map(str, unsupported))))

    return {
        "target_column": target_column,
        "positive_label": target["positive_label"],
        "included_columns": list(included),
        "column_types": resolved_types,
        "split_method": split_method,
        "test_size": test_size,
        "random_state": int(random_state),
        "time_column": time_column,
        "candidates": list(candidates),
    }


def _prepare_target(
    data_frame: pd.DataFrame, target_column: str, positive_label: Any
) -> Tuple[pd.DataFrame, pd.Series, Any]:
    target = data_frame[target_column]
    keep = target.notna()
    clean_frame = data_frame.loc[keep].copy().reset_index(drop=True)
    clean_target = clean_frame[target_column]
    labels = list(pd.unique(clean_target))
    if len(labels) != 2:
        raise ValueError(
            f"target column must contain exactly two non-missing classes; found {len(labels)}"
        )

    positive_matches = [label for label in labels if _labels_equal(label, positive_label)]
    if len(positive_matches) != 1:
        raise ValueError("target.positive_label is not an unambiguous target value")
    resolved_positive = positive_matches[0]
    mapped = clean_target.map(lambda value: int(_labels_equal(value, resolved_positive)))
    counts = mapped.value_counts()
    if set(counts.index.tolist()) != {0, 1}:
        raise ValueError("target mapping did not produce both classes")
    if int(counts.min()) < 2:
        raise ValueError("each target class must contain at least two observations")

    return clean_frame, mapped.astype(int), resolved_positive


def _labels_equal(left: Any, right: Any) -> bool:
    try:
        result = left == right
    except Exception:
        return False
    if isinstance(result, (bool, np.bool_)):
        return bool(result)
    return False


def _prepare_features(
    data_frame: pd.DataFrame,
    included_columns: Sequence[str],
    column_types: Dict[str, str],
) -> pd.DataFrame:
    for column in included_columns:
        if not data_frame[column].notna().any():
            raise ValueError(f"included feature {column!r} is entirely missing")
    features = _normalise_feature_values(data_frame, included_columns, column_types)
    for column in included_columns:
        if column_types[column] == "numeric" and not features[column].notna().any():
            raise ValueError(f"numeric feature {column!r} has no usable numeric values")
    return features


def _normalise_feature_values(
    data_frame: pd.DataFrame,
    included_columns: Sequence[str],
    column_types: Dict[str, str],
) -> pd.DataFrame:
    if not isinstance(data_frame, pd.DataFrame):
        raise TypeError("model input must be a pandas DataFrame")
    missing = [column for column in included_columns if column not in data_frame.columns]
    if missing:
        raise ValueError("model input is missing columns: {}".format(", ".join(sorted(missing))))

    features = data_frame.loc[:, list(included_columns)].copy()
    for column in included_columns:
        if column_types[column] == "numeric":
            converted = pd.to_numeric(features[column], errors="coerce")
            features[column] = converted.replace([np.inf, -np.inf], np.nan).astype(float)
        else:
            features[column] = features[column].map(
                lambda value: str(value) if pd.notna(value) else np.nan
            )
    return features


def _holdout_indices(
    data_frame: pd.DataFrame,
    target: pd.Series,
    split_method: str,
    test_size: float,
    random_state: int,
    time_column: Any,
) -> Tuple[np.ndarray, np.ndarray]:
    indices = np.arange(len(data_frame))
    if split_method == "stratified_random":
        train_indices, holdout_indices = train_test_split(
            indices,
            test_size=test_size,
            random_state=random_state,
            shuffle=True,
            stratify=target.to_numpy(),
        )
        train_indices = np.asarray(train_indices, dtype=int)
        holdout_indices = np.asarray(holdout_indices, dtype=int)
    else:
        parsed_time = pd.to_datetime(data_frame[time_column], errors="coerce", utc=True)
        if parsed_time.isna().any():
            raise ValueError("time_holdout requires every time value to be parseable")
        ordered_indices = np.argsort(parsed_time.to_numpy(), kind="mergesort")
        holdout_count = int(np.ceil(len(ordered_indices) * test_size))
        split_at = len(ordered_indices) - holdout_count
        if split_at <= 0 or split_at >= len(ordered_indices):
            raise ValueError("test_size leaves an empty train or holdout partition")
        train_indices = np.asarray(ordered_indices[:split_at], dtype=int)
        holdout_indices = np.asarray(ordered_indices[split_at:], dtype=int)

    for name, part_indices in (
        ("training", train_indices),
        ("holdout", holdout_indices),
    ):
        if len(np.unique(target.iloc[part_indices].to_numpy())) != 2:
            raise ValueError(f"{name} partition must contain both target classes")
    return train_indices, holdout_indices


def _make_preprocessor(
    included_columns: Sequence[str], column_types: Dict[str, str], scale: bool
) -> ColumnTransformer:
    numeric_columns = [column for column in included_columns if column_types[column] == "numeric"]
    categorical_columns = [
        column for column in included_columns if column_types[column] == "categorical"
    ]

    transformers: List[Tuple[str, Any, List[str]]] = []
    if numeric_columns:
        numeric_steps: List[Tuple[str, Any]] = [("imputer", SimpleImputer(strategy="median"))]
        if scale:
            numeric_steps.append(("scaler", StandardScaler()))
        transformers.append(("numeric", Pipeline(numeric_steps), numeric_columns))
    if categorical_columns:
        categorical_pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("one_hot", OneHotEncoder(handle_unknown="ignore")),
            ]
        )
        transformers.append(("categorical", categorical_pipeline, categorical_columns))

    return ColumnTransformer(transformers=transformers, remainder="drop")


def _make_candidate(
    name: str,
    included_columns: Sequence[str],
    column_types: Dict[str, str],
    random_state: int,
) -> Pipeline:
    if name == "dummy":
        estimator: Any = DummyClassifier(strategy="prior")
        scale = False
    elif name == "logistic_regression":
        estimator = LogisticRegression(
            class_weight="balanced",
            max_iter=1000,
            random_state=random_state,
            solver="liblinear",
        )
        scale = True
    elif name == "random_forest":
        estimator = RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            min_samples_leaf=5,
            class_weight="balanced_subsample",
            random_state=random_state,
            n_jobs=1,
        )
        scale = False
    else:  # Defensive: plan validation should make this unreachable.
        raise ValueError(f"unsupported candidate {name!r}")

    return Pipeline(
        [
            (
                "input_normalizer",
                _InputNormalizer(list(included_columns), dict(column_types)),
            ),
            (
                "preprocessor",
                _make_preprocessor(included_columns, column_types, scale=scale),
            ),
            ("model", estimator),
        ]
    )


def _oof_folds(
    target: pd.Series, split_method: str, random_state: int
) -> List[Tuple[np.ndarray, np.ndarray]]:
    target_array = target.to_numpy()
    if split_method == "stratified_random":
        class_counts = np.bincount(target_array, minlength=2)
        fold_count = int(min(5, class_counts.min()))
        if fold_count < 2:
            raise ValueError("training data cannot support stratified OOF validation")
        splitter = StratifiedKFold(n_splits=fold_count, shuffle=True, random_state=random_state)
        return [
            (np.asarray(train, dtype=int), np.asarray(valid, dtype=int))
            for train, valid in splitter.split(np.zeros(len(target_array)), target_array)
        ]

    if len(target_array) < 4:
        raise ValueError("training data is too small for time-series OOF validation")
    split_count = min(5, len(target_array) - 1)
    splitter = TimeSeriesSplit(n_splits=split_count)
    folds = []
    for train, valid in splitter.split(np.zeros(len(target_array))):
        if len(np.unique(target_array[train])) < 2:
            continue
        folds.append((np.asarray(train, dtype=int), np.asarray(valid, dtype=int)))
    if not folds:
        raise ValueError(
            "time-ordered training data has no OOF fold with both classes in its training window"
        )
    covered = np.concatenate([valid for _, valid in folds])
    if len(np.unique(target_array[covered])) < 2:
        raise ValueError("time-series OOF validation rows must contain both classes")
    return folds


def _positive_probability(model: Pipeline, features: pd.DataFrame) -> np.ndarray:
    probabilities = np.asarray(model.predict_proba(features), dtype=float)
    classes = list(model.classes_)
    if 1 not in classes:
        raise ValueError("trained candidate does not expose positive class 1")
    return probabilities[:, classes.index(1)]


def _candidate_parameters(name: str) -> Dict[str, Any]:
    if name == "dummy":
        return {"strategy": "prior"}
    if name == "logistic_regression":
        return {
            "class_weight": "balanced",
            "max_iter": 1000,
            "solver": "liblinear",
        }
    return {
        "n_estimators": 100,
        "max_depth": 8,
        "min_samples_leaf": 5,
        "class_weight": "balanced_subsample",
        "n_jobs": 1,
    }


def _feature_importance(model: Pipeline, limit: int = 20) -> List[Dict[str, Any]]:
    estimator = model.named_steps["model"]
    if hasattr(estimator, "coef_"):
        signed = np.asarray(estimator.coef_, dtype=float).reshape(-1)
        magnitude = np.abs(signed)
        directions: Any = signed
    elif hasattr(estimator, "feature_importances_"):
        magnitude = np.asarray(estimator.feature_importances_, dtype=float).reshape(-1)
        directions = None
    else:
        return []

    preprocessor = model.named_steps["preprocessor"]
    try:
        names = [str(name) for name in preprocessor.get_feature_names_out()]
    except (AttributeError, ValueError):
        names = [f"feature_{index}" for index in range(len(magnitude))]
    if len(names) != len(magnitude):
        names = [f"feature_{index}" for index in range(len(magnitude))]

    order = np.argsort(-magnitude, kind="mergesort")[:limit]
    result = []
    for index in order:
        item: Dict[str, Any] = {
            "feature": names[int(index)],
            "importance": float(magnitude[int(index)]),
        }
        if directions is not None:
            item["coefficient"] = float(directions[int(index)])
        result.append(item)
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        numeric = float(value)
        return numeric if np.isfinite(numeric) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _plan_digest(plan: Dict[str, Any]) -> str:
    # Keep this compatible with the approval hash: the embedded digest cannot
    # be part of the content from which that same digest is computed.
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    canonical = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def train_candidates(df: pd.DataFrame, plan: Dict[str, Any]) -> Tuple[Dict[str, Any], object]:
    """Train approved candidates and evaluate one champion on a fixed holdout.

    Candidate selection and threshold selection use only out-of-fold predictions
    from the training partition.  The returned estimator is fitted on the full
    training partition; the fixed holdout is used once for final evaluation.
    """

    config = _normalise_plan(df, plan)
    clean_frame, target, resolved_positive = _prepare_target(
        df, config["target_column"], config["positive_label"]
    )
    features = _prepare_features(clean_frame, config["included_columns"], config["column_types"])
    train_indices, holdout_indices = _holdout_indices(
        clean_frame,
        target,
        config["split_method"],
        config["test_size"],
        config["random_state"],
        config["time_column"],
    )

    train_features = features.iloc[train_indices].reset_index(drop=True)
    train_target = target.iloc[train_indices].reset_index(drop=True)
    holdout_features = features.iloc[holdout_indices].reset_index(drop=True)
    holdout_target = target.iloc[holdout_indices].reset_index(drop=True)

    # Time-based folds require chronological training rows.  The fixed split
    # already selected the earliest rows, but their original order may not be
    # chronological when the input CSV is unsorted.
    if config["split_method"] == "time_holdout":
        train_times = pd.to_datetime(
            clean_frame.iloc[train_indices][config["time_column"]], utc=True
        )
        order = np.argsort(train_times.to_numpy(), kind="mergesort")
        train_features = train_features.iloc[order].reset_index(drop=True)
        train_target = train_target.iloc[order].reset_index(drop=True)

    folds = _oof_folds(train_target, config["split_method"], config["random_state"])
    candidate_rows: List[Dict[str, Any]] = []
    oof_predictions: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}

    for candidate_name in config["candidates"]:
        prototype = _make_candidate(
            candidate_name,
            config["included_columns"],
            config["column_types"],
            config["random_state"],
        )
        predictions = np.full(len(train_target), np.nan, dtype=float)
        for fold_train, fold_valid in folds:
            fitted_fold = clone(prototype)
            fitted_fold.fit(train_features.iloc[fold_train], train_target.iloc[fold_train])
            predictions[fold_valid] = _positive_probability(
                fitted_fold, train_features.iloc[fold_valid]
            )

        valid_mask = np.isfinite(predictions)
        valid_target = train_target.to_numpy()[valid_mask]
        valid_probability = predictions[valid_mask]
        if len(np.unique(valid_target)) != 2:
            raise ValueError("OOF predictions must cover both target classes")
        metrics = evaluate_probabilities(valid_target, valid_probability, threshold=0.5)
        candidate_rows.append(
            {
                "candidate": candidate_name,
                "name": candidate_name,
                "display_name": CANDIDATE_DISPLAY_NAMES[candidate_name],
                "oof_metrics": metrics,
                "roc_auc": metrics["roc_auc"],
                "ks": metrics["ks"],
                "pr_auc": metrics["pr_auc"],
                "oof_rows": int(valid_mask.sum()),
                "oof_coverage": float(valid_mask.mean()),
            }
        )
        oof_predictions[candidate_name] = (valid_target, valid_probability)

    # Prefer the simpler supported model after the approved discrimination
    # metrics tie, rather than letting incidental input order decide.
    champion_row = max(
        candidate_rows,
        key=lambda row: (
            row["oof_metrics"]["roc_auc"],
            row["oof_metrics"]["ks"],
            -SUPPORTED_CANDIDATES.index(row["candidate"]),
        ),
    )
    champion_name = champion_row["candidate"]
    champion_oof_target, champion_oof_probability = oof_predictions[champion_name]
    threshold = best_ks_threshold(champion_oof_target, champion_oof_probability)

    champion_model = _make_candidate(
        champion_name,
        config["included_columns"],
        config["column_types"],
        config["random_state"],
    )
    champion_model.fit(train_features, train_target)
    holdout_probability = _positive_probability(champion_model, holdout_features)
    holdout_metrics = evaluate_probabilities(
        holdout_target.to_numpy(), holdout_probability, threshold=threshold
    )
    holdout_metrics["lift_table"] = lift_table(
        holdout_target.to_numpy(), holdout_probability, bins=10
    )

    result = {
        "candidate_comparison": candidate_rows,
        "champion": {
            "name": champion_name,
            "display_name": CANDIDATE_DISPLAY_NAMES[champion_name],
            "selection_metric": "oof_roc_auc",
            "tie_breaker": "oof_ks",
            "final_tie_breaker": "simpler_model",
        },
        "holdout_metrics": holdout_metrics,
        "threshold": {
            "value": threshold,
            "strategy": "max_ks_on_champion_oof",
            "source": "training_partition_oof_only",
        },
        "feature_importance": _feature_importance(champion_model),
        "reproducibility": {
            "random_state": config["random_state"],
            "split_method": config["split_method"],
            "test_size": config["test_size"],
            "train_rows": int(len(train_features)),
            "holdout_rows": int(len(holdout_features)),
            "rows_with_missing_target_removed": int(len(df) - len(clean_frame)),
            "positive_label": _json_safe(resolved_positive),
            "oof_folds": int(len(folds)),
            "oof_rows": int(champion_row["oof_rows"]),
            "oof_coverage": float(champion_row["oof_coverage"]),
            "candidate_parameters": {
                name: _candidate_parameters(name) for name in config["candidates"]
            },
            "plan_sha256": _plan_digest(plan),
            "versions": {
                "pandas": pd.__version__,
                "scikit_learn": sklearn.__version__,
                "numpy": np.__version__,
            },
        },
        "limitations": [
            (
                "Holdout metrics are an offline estimate and do not demonstrate "
                "production performance or business impact."
            ),
            (
                "Candidate hyperparameters are deterministic defaults; this MVP "
                "does not perform automated tuning."
            ),
            (
                "The workflow does not establish probability calibration, fairness, "
                "causal validity, or stability over time."
            ),
            "Feature importance is model-specific and must not be interpreted as causal influence.",
        ],
    }
    return _json_safe(result), champion_model
