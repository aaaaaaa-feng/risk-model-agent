"""Governed construction and approval of a deterministic modeling plan."""

import hashlib
import hmac
import json
import math
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

import pandas as pd

from app.domain import DomainError

_POST_OUTCOME_TOKENS = (
    "days_past_due",
    "past_due",
    "overdue",
    "delinquen",
    "collection",
    "chargeoff",
    "charge_off",
    "writeoff",
    "write_off",
    "repayment_status",
    "repay_status",
    "bad_debt",
    "first_payment_default",
    "first_overdue",
    "逾期",
    "催收",
    "核销",
    "坏账结果",
    "还款状态",
    "首逾",
)
_HISTORICAL_FEATURE_PREFIXES = ("prior_", "historical_", "pre_application_")
_REQUIRED_CONFIRMATIONS = [
    "target_definition",
    "feature_and_leakage_review",
    "split_strategy",
    "model_plan",
]


def build_plan(
    df: pd.DataFrame,
    profile: Dict[str, Any],
    request: Dict[str, Any],
    version: int,
) -> Dict[str, Any]:
    """Build a reviewable plan; it never trains or silently fixes blockers."""

    if not isinstance(df, pd.DataFrame):
        raise DomainError(400, "INVALID_DATAFRAME", "Expected a pandas DataFrame.")
    if not isinstance(profile, dict) or not isinstance(profile.get("columns"), dict):
        raise DomainError(400, "INVALID_PROFILE", "A valid dataframe profile is required.")
    if not isinstance(request, dict):
        raise DomainError(400, "INVALID_PLAN_REQUEST", "Plan request must be an object.")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise DomainError(400, "INVALID_PLAN_VERSION", "Plan version must be positive.")
    dataframe_columns = [str(column) for column in df.columns]
    if profile.get("row_count") != len(df) or set(profile["columns"]) != set(dataframe_columns):
        raise DomainError(
            409,
            "PROFILE_DATA_MISMATCH",
            "The profile does not describe the current dataframe.",
        )

    warnings: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    target_request = request.get("target", {})
    if not isinstance(target_request, dict):
        target_request = {}
    target_column = target_request.get("column", request.get("target_column"))
    positive_label = target_request.get("positive_label", request.get("positive_label", 1))
    negative_label_provided = "negative_label" in target_request or "negative_label" in request
    negative_label = target_request.get("negative_label", request.get("negative_label"))

    if not isinstance(target_column, str) or not target_column:
        raise DomainError(
            422,
            "TARGET_COLUMN_REQUIRED",
            "A target column must be selected before planning.",
        )
    if target_column not in df.columns:
        raise DomainError(
            422,
            "TARGET_COLUMN_NOT_FOUND",
            "The selected target column is not present in the dataset.",
            {"column": target_column},
        )
    target_series = df[target_column]
    if not negative_label_provided:
        remaining_labels = [
            value
            for value in target_series.dropna().drop_duplicates().tolist()
            if not _labels_equal(value, positive_label)
        ]
        if len(remaining_labels) == 1:
            negative_label = remaining_labels[0]
    if negative_label_provided and _labels_equal(positive_label, negative_label):
        raise DomainError(
            422,
            "TARGET_LABELS_IDENTICAL",
            "Positive and negative labels must be different.",
        )

    positive_mask = _label_mask(target_series, positive_label)
    negative_mask = (
        _label_mask(target_series, negative_label)
        if negative_label is not None
        else pd.Series(False, index=target_series.index)
    )
    missing_count = int(target_series.isna().sum())
    recognized_mask = positive_mask | negative_mask | target_series.isna()
    unexpected = target_series[~recognized_mask]
    positive_count = int(positive_mask.sum())
    negative_count = int(negative_mask.sum())

    if missing_count:
        blocking.append(
            _issue(
                "TARGET_HAS_MISSING_VALUES",
                "Target contains missing values; exclusion or label policy must be resolved.",
                [target_column],
                {"missing_count": missing_count},
            )
        )
    if not unexpected.empty:
        labels = [_json_safe(value) for value in unexpected.drop_duplicates().head(20)]
        blocking.append(
            _issue(
                "TARGET_NOT_BINARY",
                "Target contains values outside the approved positive/negative labels.",
                [target_column],
                {
                    "unexpected_count": int(unexpected.shape[0]),
                    "unexpected_labels": labels,
                    "values_truncated": int(unexpected.nunique(dropna=True)) > len(labels),
                },
            )
        )
    if positive_count == 0 or negative_count == 0:
        blocking.append(
            _issue(
                "TARGET_CLASS_MISSING",
                "Both target classes must be present.",
                [target_column],
                {
                    "positive_count": positive_count,
                    "negative_count": negative_count,
                },
            )
        )
    elif min(positive_count, negative_count) < 20:
        blocking.append(
            _issue(
                "TARGET_MINORITY_TOO_SMALL",
                "The minority target class has fewer than 20 rows.",
                [target_column],
                {
                    "positive_count": positive_count,
                    "negative_count": negative_count,
                },
            )
        )

    split_request = request.get("split", {})
    if not isinstance(split_request, dict):
        split_request = {}
    time_column = split_request.get("time_column", request.get("time_column"))
    default_method = "time_holdout" if time_column else "stratified_random"
    split_method = split_request.get("method", default_method)
    if split_method == "time":
        # Normalize an intuitive input alias while keeping the plan contract stable.
        split_method = "time_holdout"
    test_size = split_request.get("test_size", 0.20)
    random_state = split_request.get("random_state", 42)
    if split_method not in {"time_holdout", "stratified_random"}:
        raise DomainError(
            422,
            "INVALID_SPLIT_METHOD",
            "Split method must be 'time_holdout' or 'stratified_random'.",
        )
    if (
        not isinstance(test_size, (int, float))
        or isinstance(test_size, bool)
        or not 0 < float(test_size) < 0.5
    ):
        raise DomainError(
            422,
            "INVALID_TEST_SIZE",
            "test_size must be greater than 0 and less than 0.5.",
        )
    if not isinstance(random_state, int) or isinstance(random_state, bool):
        raise DomainError(
            422,
            "INVALID_RANDOM_STATE",
            "random_state must be an integer.",
        )
    if split_method == "time_holdout":
        if not isinstance(time_column, str) or not time_column:
            blocking.append(
                _issue(
                    "TIME_COLUMN_REQUIRED",
                    "Time split requires an explicit decision-time column.",
                )
            )
        elif time_column not in df.columns:
            blocking.append(
                _issue(
                    "TIME_COLUMN_NOT_FOUND",
                    "Selected time column is not present in the dataset.",
                    [time_column],
                )
            )
    else:
        time_column = None
        warnings.append(
            _issue(
                "NO_TIME_BASED_OOT",
                "Random split is not an independent out-of-time validation.",
            )
        )

    requested_features = request.get("included_columns")
    if requested_features is None:
        requested_features = request.get("features")
        if isinstance(requested_features, dict):
            requested_features = requested_features.get("included_columns")
    if requested_features is None:
        feature_names = [str(column) for column in df.columns if column != target_column]
    elif isinstance(requested_features, list) and all(
        isinstance(item, str) for item in requested_features
    ):
        feature_names = list(dict.fromkeys(requested_features))
        missing_features = [name for name in feature_names if name not in df.columns]
        if missing_features:
            raise DomainError(
                422,
                "FEATURE_COLUMN_NOT_FOUND",
                "One or more requested feature columns are missing.",
                {"columns": missing_features},
            )
        feature_names = [name for name in feature_names if name != target_column]
    else:
        raise DomainError(
            422,
            "INVALID_FEATURE_COLUMNS",
            "included_columns must be a list of column names.",
        )

    included: List[str] = []
    dropped: List[str] = []
    column_types: Dict[str, str] = {}
    for name in feature_names:
        column_profile = profile["columns"].get(name, {})
        inferred_type = str(column_profile.get("inferred_type", "unknown"))
        reasons: List[str] = []
        if bool(column_profile.get("is_constant")):
            reasons.append("constant")
        if bool(column_profile.get("is_high_missing")):
            reasons.append("high_missing")
        if bool(column_profile.get("is_sensitive")):
            reasons.append("sensitive")
        elif bool(column_profile.get("is_suspected_id")):
            reasons.append("suspected_id")
        if bool(column_profile.get("is_datetime")):
            reasons.append("datetime")
        if time_column is not None and name == time_column:
            reasons.append("split_time_column")
        if bool(column_profile.get("is_high_cardinality")):
            reasons.append("high_cardinality")

        normalized_name = name.strip().lower()
        post_outcome_match = any(token in normalized_name for token in _POST_OUTCOME_TOKENS)
        historical_prefix = normalized_name.startswith(_HISTORICAL_FEATURE_PREFIXES)
        exact_target_copy = _is_exact_target_copy(df[name], target_series)
        if post_outcome_match and historical_prefix:
            warnings.append(
                _issue(
                    "HISTORICAL_RISK_FEATURE_REVIEW",
                    (
                        "Feature is named as historical risk information; confirm it was "
                        "available before the application decision."
                    ),
                    [name],
                    {"detection": "heuristic_name_match"},
                )
            )
        elif post_outcome_match:
            reasons.append("suspected_post_outcome")
            blocking.append(
                _issue(
                    "SUSPECTED_POST_OUTCOME_FEATURE",
                    "Feature name suggests information produced after the credit decision.",
                    [name],
                    {"detection": "heuristic_name_match"},
                )
            )
        if exact_target_copy:
            reasons.append("target_copy")
            blocking.append(
                _issue(
                    "TARGET_COPY_DETECTED",
                    "Feature reproduces the target on all comparable non-missing rows.",
                    [name],
                    {"detection": "heuristic_value_comparison"},
                )
            )

        if reasons:
            dropped.append(name)
            warnings.append(
                _issue(
                    "FEATURE_DROPPED",
                    "Feature was excluded by the default governance policy.",
                    [name],
                    {"reasons": sorted(set(reasons))},
                )
            )
        else:
            included.append(name)
            column_types[name] = "numeric" if inferred_type == "numeric" else "categorical"

    if not included:
        blocking.append(
            _issue(
                "NO_ELIGIBLE_FEATURES",
                "No eligible modeling features remain after governance checks.",
            )
        )

    limitations = [
        (
            "Leakage detection is heuristic and cannot prove that a feature was "
            "available at decision time; business-owner review is required."
        ),
        (
            "Offline discrimination and calibration metrics do not demonstrate "
            "production approval, causal business impact, or reduced bad debt."
        ),
        (
            "The plan does not perform reject inference or establish that labeled "
            "customers represent the full applicant population."
        ),
    ]
    if bool(profile.get("truncated")):
        limitations.append(
            "Profiling was truncated to a bounded row sample and may miss rare issues."
        )
    if split_method != "time_holdout":
        limitations.append(
            "No out-of-time validation is defined; stability over time remains untested."
        )

    plan: Dict[str, Any] = {
        "version": version,
        "target": {
            "column": target_column,
            "positive_label": _json_safe(positive_label),
            "negative_label": _json_safe(negative_label),
            "positive_count": positive_count,
            "negative_count": negative_count,
        },
        "features": {
            "included_columns": included,
            "dropped_columns": dropped,
            "column_types": column_types,
        },
        "split": {
            "method": split_method,
            "test_size": float(test_size),
            "random_state": random_state,
            "time_column": time_column,
        },
        "preprocessing": {
            "numeric_missing": "median",
            "categorical_missing": "most_frequent",
            "categorical_encoding": "one_hot_handle_unknown",
            "numeric_scaling": "standard_for_logistic_regression",
            "fit_scope": "train_only",
        },
        "candidates": ["dummy", "logistic_regression", "random_forest"],
        "selection": {
            "primary_metric": "roc_auc",
            "guardrail_metrics": ["ks", "brier_score", "calibration"],
            "prefer_simpler_model_on_tie": True,
            "test_set_locked_until_selection": True,
        },
        "warnings": _deduplicate_issues(warnings),
        "blocking_issues": _deduplicate_issues(blocking),
        "required_confirmations": list(_REQUIRED_CONFIRMATIONS),
        "limitations": limitations,
        "plan_hash": "",
    }
    plan["plan_hash"] = hash_plan(plan)
    return plan


def hash_plan(plan: Dict[str, Any]) -> str:
    """Hash canonical plan content, excluding its self-referential hash field."""

    if not isinstance(plan, dict):
        raise DomainError(400, "INVALID_PLAN", "Plan must be an object.")
    payload = deepcopy(plan)
    payload.pop("plan_hash", None)
    try:
        canonical = json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DomainError(
            422,
            "PLAN_HASH_FAILED",
            "Plan contains values that cannot be hashed deterministically.",
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


def validate_approval(
    plan: Dict[str, Any],
    supplied_hash: str,
    confirmations: Any,
) -> bool:
    """Validate a human approval bound to the exact, unblocked plan."""

    expected_hash = hash_plan(plan)
    embedded_hash = plan.get("plan_hash") if isinstance(plan, dict) else None
    if (
        not isinstance(supplied_hash, str)
        or not hmac.compare_digest(supplied_hash, expected_hash)
        or not isinstance(embedded_hash, str)
        or not hmac.compare_digest(embedded_hash, expected_hash)
    ):
        raise DomainError(
            409,
            "PLAN_HASH_MISMATCH",
            "Approval does not match the current modeling plan.",
        )
    blocking = plan.get("blocking_issues", [])
    if blocking:
        raise DomainError(
            409,
            "PLAN_BLOCKED",
            "Blocking issues must be resolved before approval.",
            {"blocking_issues": blocking},
        )

    confirmed = _confirmed_names(confirmations)
    required = plan.get("required_confirmations", [])
    missing = [name for name in required if name not in confirmed]
    if missing:
        raise DomainError(
            422,
            "CONFIRMATIONS_INCOMPLETE",
            "All required human confirmations must be supplied.",
            {"missing": missing},
        )
    return True


def _confirmed_names(confirmations: Any) -> Set[str]:
    confirmed: Set[str] = set()
    if isinstance(confirmations, Mapping):
        for name, value in confirmations.items():
            if value is True or (isinstance(value, Mapping) and value.get("confirmed") is True):
                confirmed.add(str(name))
        return confirmed
    if isinstance(confirmations, (list, tuple, set)):
        for item in confirmations:
            if isinstance(item, str):
                confirmed.add(item)
            elif isinstance(item, Mapping) and item.get("confirmed") is True:
                name = item.get("id", item.get("name"))
                if isinstance(name, str):
                    confirmed.add(name)
        return confirmed
    raise DomainError(
        422,
        "INVALID_CONFIRMATIONS",
        "Confirmations must be a mapping or list.",
    )


def _label_mask(series: pd.Series, label: Any) -> pd.Series:
    try:
        mask = series.eq(label)
    except (TypeError, ValueError):
        mask = series.astype(str).eq(str(label))
    return mask.fillna(False)


def _labels_equal(left: Any, right: Any) -> bool:
    try:
        result = left == right
        return bool(result) if not pd.isna(result) else False
    except (TypeError, ValueError):
        return str(left) == str(right)


def _is_exact_target_copy(feature: pd.Series, target: pd.Series) -> bool:
    comparable = feature.notna() & target.notna()
    if int(comparable.sum()) < 20:
        return False
    feature_values = feature[comparable]
    target_values = target[comparable]
    try:
        direct = feature_values.eq(target_values).fillna(False)
        if bool(direct.all()):
            return True
    except (TypeError, ValueError):
        pass
    normalized_feature = feature_values.astype(str).str.strip().str.lower()
    normalized_target = target_values.astype(str).str.strip().str.lower()
    return bool(normalized_feature.eq(normalized_target).all())


def _json_safe(value: Any) -> Any:
    if value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _issue(
    code: str,
    message: str,
    columns: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "columns": columns or [],
        "details": details or {},
    }


def _deduplicate_issues(issues: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for issue in issues:
        key = json.dumps(_json_safe(issue), ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
