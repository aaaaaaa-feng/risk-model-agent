from __future__ import annotations

import math
from typing import Any, Sequence

import pandas as pd

from app.core.security import is_pii_column

from .profiling import ID_PATTERN, infer_type, leakage_flags


RECOVERABLE_REASONS = {"MISSING_RATE", "LOW_IV", "HIGH_CORRELATION", "UNSUPPORTED_TYPE"}
NON_RECOVERABLE_REASONS = {
    "PII",
    "LEAKAGE",
    "TARGET",
    "OTHER_TARGET",
    "IDENTIFIER",
    "CONSTANT",
}


def _bin_for_iv(series: pd.Series, max_bins: int = 10) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > max_bins:
        try:
            result = pd.qcut(series, q=max_bins, duplicates="drop").astype(str)
        except (ValueError, TypeError):
            result = series.astype(str)
    else:
        values = series.astype("object").where(series.notna(), "<MISSING>").astype(str)
        counts = values.value_counts()
        rare = set(counts[counts < max(20, int(len(values) * 0.005))].index)
        result = values.map(lambda value: "<RARE>" if value in rare else value)
    return result.astype("object").where(series.notna(), "<MISSING>").astype(str)


def calculate_iv(
    series: pd.Series, target: pd.Series, max_bins: int = 10
) -> tuple[float, list[dict[str, Any]]]:
    valid = target.isin([0, 1])
    y = target.loc[valid].astype(int)
    bins = _bin_for_iv(series.loc[valid], max_bins)
    total_good = max(int((y == 0).sum()), 1)
    total_bad = max(int((y == 1).sum()), 1)
    rows: list[dict[str, Any]] = []
    iv = 0.0
    for label, indices in bins.groupby(bins).groups.items():
        bin_y = y.loc[indices]
        good = int((bin_y == 0).sum())
        bad = int((bin_y == 1).sum())
        good_share = (good + 0.5) / (total_good + 0.5 * len(bins.unique()))
        bad_share = (bad + 0.5) / (total_bad + 0.5 * len(bins.unique()))
        woe = math.log(good_share / bad_share)
        contribution = (good_share - bad_share) * woe
        iv += contribution
        rows.append(
            {
                "bin": str(label),
                "count": good + bad,
                "good": good,
                "bad": bad,
                "bad_rate": bad / (good + bad) if good + bad else None,
                "woe": woe,
                "iv": contribution,
            }
        )
    return float(max(iv, 0)), rows


def _base_reason(
    column: str, series: pd.Series, target: str, missing_threshold: float
) -> str | None:
    if column == target:
        return "TARGET"
    if is_pii_column(column):
        return "PII"
    # Entity and transaction keys remain identifiers even when they repeat. A
    # many-orders-per-customer table must not turn customer_id into a restorable
    # text feature merely because its uniqueness ratio is below 80%.
    if ID_PATTERN.search(column):
        return "IDENTIFIER"
    if series.nunique(dropna=True) <= 1:
        return "CONSTANT"
    if series.isna().mean() > missing_threshold:
        return "MISSING_RATE"
    if infer_type(series) in {"datetime", "empty", "text"}:
        return "UNSUPPORTED_TYPE"
    return None


def screen_features(
    train: pd.DataFrame,
    target: str,
    candidates: Sequence[str] | None = None,
    protected_targets: Sequence[str] = (),
    iv_threshold: float = 0.02,
    missing_threshold: float = 0.30,
    correlation_threshold: float = 0.70,
) -> dict[str, Any]:
    columns = list(candidates or [column for column in train.columns if column != target])
    protected = set(protected_targets) - {target}
    leakage = set(leakage_flags(columns, target)["blocked"])
    results: list[dict[str, Any]] = []
    iv_values: dict[str, float] = {}
    for column in columns:
        if column not in train:
            continue
        series = train[column]
        if column in protected:
            reason = "OTHER_TARGET"
        else:
            reason = (
                "LEAKAGE"
                if column in leakage
                else _base_reason(column, series, target, missing_threshold)
            )
        iv = None
        bins: list[dict[str, Any]] = []
        if reason is None or reason in RECOVERABLE_REASONS:
            try:
                iv, bins = calculate_iv(series, train[target])
                iv_values[column] = iv
                if reason is None and iv < iv_threshold:
                    reason = "LOW_IV"
            except (ValueError, TypeError, OverflowError):
                reason = reason or "UNSUPPORTED_TYPE"
        results.append(
            {
                "column": column,
                "type": infer_type(series),
                "missing_rate": float(series.isna().mean()),
                "iv": iv,
                "iv_bins": bins,
                "status": "included" if reason is None else "excluded",
                "reason": reason,
                "recoverable": reason in RECOVERABLE_REASONS,
                "fit_scope": "train_only",
            }
        )
    included = [item["column"] for item in results if item["status"] == "included"]
    numeric = [column for column in included if pd.api.types.is_numeric_dtype(train[column])]
    correlated: set[str] = set()
    correlation_pairs: list[dict[str, Any]] = []
    if len(numeric) > 1:
        matrix = train[numeric].corr(method="spearman").abs()
        for left_index, left in enumerate(numeric):
            for right in numeric[left_index + 1 :]:
                value = matrix.loc[left, right]
                if pd.notna(value) and value > correlation_threshold:
                    drop = right if iv_values.get(left, 0) >= iv_values.get(right, 0) else left
                    correlated.add(drop)
                    correlation_pairs.append(
                        {
                            "left": left,
                            "right": right,
                            "correlation": float(value),
                            "excluded": drop,
                        }
                    )
    for item in results:
        if item["column"] in correlated and item["status"] == "included":
            item.update(status="excluded", reason="HIGH_CORRELATION", recoverable=True)
    return {
        "thresholds": {
            "iv": iv_threshold,
            "missing_rate": missing_threshold,
            "correlation": correlation_threshold,
        },
        "features": results,
        "included": [item["column"] for item in results if item["status"] == "included"],
        "excluded": [item for item in results if item["status"] == "excluded"],
        "correlation_pairs": correlation_pairs,
        "fit_scope": "train_only",
    }


def restore_features(
    screening: dict[str, Any], requests: Sequence[dict[str, str]]
) -> dict[str, Any]:
    requested = {item.get("column"): item.get("reason", "").strip() for item in requests}
    restored: list[dict[str, str]] = []
    for item in screening["features"]:
        if item["column"] not in requested:
            continue
        if not item["recoverable"] or item["reason"] in NON_RECOVERABLE_REASONS:
            raise ValueError(f"FEATURE_NOT_RECOVERABLE: {item['column']}")
        reason = requested[item["column"]]
        if len(reason) < 8:
            raise ValueError(f"RESTORE_REASON_REQUIRED: {item['column']}")
        item.update(status="included", restored=True, restore_reason=reason)
        restored.append({"column": item["column"], "reason": reason})
    screening["included"] = [
        item["column"] for item in screening["features"] if item["status"] == "included"
    ]
    screening["excluded"] = [item for item in screening["features"] if item["status"] == "excluded"]
    screening["restored"] = restored
    return screening
