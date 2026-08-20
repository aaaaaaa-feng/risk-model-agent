from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Sequence

import numpy as np
import pandas as pd


def _woe_table(labels: pd.Series, target: pd.Series) -> tuple[list[dict[str, Any]], float]:
    frame = pd.DataFrame({"bin": labels.astype(str), "target": target.astype(int)})
    grouped = frame.groupby("bin", dropna=False)["target"].agg(["count", "sum"]).reset_index()
    total_bad = max(int(grouped["sum"].sum()), 1)
    total_good = max(int((grouped["count"] - grouped["sum"]).sum()), 1)
    count = len(grouped)
    rows: list[dict[str, Any]] = []
    total_iv = 0.0
    for _, row in grouped.iterrows():
        bad = int(row["sum"])
        good = int(row["count"] - row["sum"])
        bad_share = (bad + 0.5) / (total_bad + 0.5 * count)
        good_share = (good + 0.5) / (total_good + 0.5 * count)
        woe = math.log(good_share / bad_share)
        iv = (good_share - bad_share) * woe
        total_iv += iv
        rows.append(
            {
                "bin": str(row["bin"]),
                "count": int(row["count"]),
                "bad": bad,
                "good": good,
                "bad_rate": bad / int(row["count"]) if row["count"] else None,
                "woe": float(woe),
                "iv": float(iv),
            }
        )
    return rows, float(total_iv)


def _is_monotonic(values: Sequence[float]) -> bool:
    clean = [value for value in values if pd.notna(value)]
    if len(clean) <= 2:
        return True
    differences = np.diff(clean)
    return bool(np.all(differences >= -1e-12) or np.all(differences <= 1e-12))


def fit_numeric_bins(
    series: pd.Series,
    target: pd.Series,
    max_bins: int = 10,
    min_bins: int = 2,
    min_bin_fraction: float = 0.03,
) -> dict[str, Any]:
    numeric = pd.to_numeric(series, errors="coerce")
    non_null = numeric.dropna()
    if non_null.nunique() <= 1:
        raise ValueError("NUMERIC_BINNING_CONSTANT")
    quantiles = np.linspace(0, 1, min(max_bins, non_null.nunique()) + 1)
    edges = np.unique(non_null.quantile(quantiles).to_numpy(dtype=float))
    if len(edges) < 3:
        edges = np.array([non_null.min(), non_null.max()], dtype=float)
    inner = list(edges[1:-1])

    def labels_for(boundaries: list[float]) -> pd.Series:
        labels = pd.cut(numeric, [-np.inf, *boundaries, np.inf], include_lowest=True).astype(str)
        return labels.where(numeric.notna(), "<MISSING>")

    while True:
        labels = labels_for(inner)
        table, iv = _woe_table(labels, target)
        ordered = [row for row in table if row["bin"] != "<MISSING>"]
        rates = [row["bad_rate"] for row in ordered]
        too_small = [row for row in ordered if row["count"] / len(series) < min_bin_fraction]
        if (_is_monotonic(rates) and not too_small) or len(inner) + 1 <= min_bins:
            break
        if len(inner) == 0:
            break
        distances = [abs(rates[index + 1] - rates[index]) for index in range(len(rates) - 1)]
        remove_index = int(np.argmin(distances)) if distances else 0
        inner.pop(min(remove_index, len(inner) - 1))
    return {
        "kind": "numeric",
        "edges": [float(value) for value in inner],
        "missing_bin": True,
        "table": table,
        "iv": iv,
        "monotonic": _is_monotonic([row["bad_rate"] for row in ordered]),
        "source": "auto_monotonic",
    }


def fit_categorical_bins(
    series: pd.Series, target: pd.Series, max_bins: int = 8, rare_fraction: float = 0.01
) -> dict[str, Any]:
    values = series.astype("object").where(series.notna(), "<MISSING>").astype(str)
    counts = values.value_counts()
    rare = set(counts[counts < max(20, int(len(values) * rare_fraction))].index)
    normalized = values.map(lambda value: "<RARE>" if value in rare and value != "<MISSING>" else value)
    rates = pd.DataFrame({"value": normalized, "target": target.astype(int)}).groupby("value")["target"].agg(["mean", "count"])
    rates = rates.sort_values("mean")
    groups: list[list[str]] = []
    if len(rates) <= max_bins:
        groups = [[str(value)] for value in rates.index]
    else:
        splits = np.array_split(list(rates.index), max_bins)
        groups = [[str(value) for value in group] for group in splits]
    lookup = {value: f"G{index + 1:02d}" for index, group in enumerate(groups) for value in group}
    labels = normalized.map(lookup)
    table, iv = _woe_table(labels, target)
    return {
        "kind": "categorical",
        "groups": groups,
        "rare_values": sorted(rare),
        "table": table,
        "iv": iv,
        "monotonic": _is_monotonic([row["bad_rate"] for row in table]),
        "source": "auto_bad_rate_order",
    }


def fit_binning(frame: pd.DataFrame, target: str, features: Sequence[str]) -> dict[str, Any]:
    specs: dict[str, dict[str, Any]] = {}
    for column in features:
        if pd.api.types.is_numeric_dtype(frame[column]):
            specs[column] = fit_numeric_bins(frame[column], frame[target])
        else:
            specs[column] = fit_categorical_bins(frame[column], frame[target])
    payload = json.dumps(specs, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "version": "bin_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12],
        "specs": specs,
        "fit_scope": "train_only",
    }


def validate_manual_spec(spec: dict[str, Any]) -> None:
    if spec.get("kind") == "numeric":
        edges = [float(value) for value in spec.get("edges", [])]
        if edges != sorted(set(edges)):
            raise ValueError("MANUAL_NUMERIC_EDGES_INVALID")
    elif spec.get("kind") == "categorical":
        groups = spec.get("groups") or []
        values = [str(value) for group in groups for value in group]
        if len(values) != len(set(values)):
            raise ValueError("MANUAL_CATEGORY_OVERLAP")
    else:
        raise ValueError("MANUAL_BIN_KIND_INVALID")


def apply_manual_binning(
    binning: dict[str, Any], frame: pd.DataFrame, target: str, column: str, spec: dict[str, Any]
) -> dict[str, Any]:
    if column not in frame:
        raise ValueError("MANUAL_BIN_COLUMN_NOT_FOUND")
    validate_manual_spec(spec)
    labels = apply_bin(frame[column], spec)
    table, iv = _woe_table(labels, frame[target])
    monotonic = _is_monotonic([row["bad_rate"] for row in table if row["bin"] != "<MISSING>"])
    if not monotonic:
        raise ValueError("MANUAL_BIN_NOT_MONOTONIC")
    updated = {
        **spec,
        "table": table,
        "iv": iv,
        "monotonic": True,
        "source": "manual",
    }
    binning["specs"][column] = updated
    payload = json.dumps(binning["specs"], ensure_ascii=False, sort_keys=True, default=str)
    binning["version"] = "bin_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    binning["invalidates"] = ["training", "review", "report"]
    return binning


def apply_bin(series: pd.Series, spec: dict[str, Any]) -> pd.Series:
    if spec["kind"] == "numeric":
        numeric = pd.to_numeric(series, errors="coerce")
        labels = pd.cut(numeric, [-np.inf, *spec.get("edges", []), np.inf], include_lowest=True).astype(str)
        return labels.where(numeric.notna(), "<MISSING>")
    values = series.astype("object").where(series.notna(), "<MISSING>").astype(str)
    rare = set(spec.get("rare_values", []))
    values = values.map(lambda value: "<RARE>" if value in rare and value != "<MISSING>" else value)
    lookup = {
        str(value): f"G{index + 1:02d}"
        for index, group in enumerate(spec.get("groups", []))
        for value in group
    }
    return values.map(lambda value: lookup.get(value, "<OTHER>"))


def woe_transform(frame: pd.DataFrame, binning: dict[str, Any]) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column, spec in binning["specs"].items():
        mapping = {row["bin"]: row["woe"] for row in spec.get("table", [])}
        output[column] = apply_bin(frame[column], spec).map(mapping).fillna(0.0).astype(float)
    return output


def bin_report(
    frame: pd.DataFrame,
    target: str,
    binning: dict[str, Any],
    dataset_name: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    overall = float(frame[target].mean()) if len(frame) else None
    for column, spec in binning["specs"].items():
        labels = apply_bin(frame[column], spec)
        table, iv = _woe_table(labels, frame[target])
        for row in table:
            row.update(
                dataset=dataset_name,
                feature=column,
                overall_bad_rate=overall,
                lift=row["bad_rate"] / overall if overall and row["bad_rate"] is not None else None,
                feature_iv=iv,
            )
            result.append(row)
    return result
