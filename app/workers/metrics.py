from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
    roc_curve,
)


def binary_metrics(y_true: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=int)
    probability = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    if len(np.unique(y)) < 2:
        return {
            "rows": len(y),
            "positive_count": int(y.sum()),
            "negative_count": int(len(y) - y.sum()),
            "bad_rate": float(y.mean()) if len(y) else None,
            "roc_auc": None,
            "ks": None,
            "pr_auc": None,
            "brier": None,
            "log_loss": None,
        }
    fpr, tpr, thresholds = roc_curve(y, probability)
    ks_index = int(np.argmax(tpr - fpr))
    return {
        "rows": len(y),
        "positive_count": int(y.sum()),
        "negative_count": int(len(y) - y.sum()),
        "bad_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, probability)),
        "ks": float((tpr - fpr)[ks_index]),
        "ks_threshold": finite(thresholds[ks_index]),
        "pr_auc": float(average_precision_score(y, probability)),
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability)),
    }


def lift_table(y_true: np.ndarray, probability: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"target": y_true, "probability": probability}).sort_values(
        "probability", ascending=False
    )
    count = min(bins, max(1, len(frame)))
    frame["bucket"] = pd.qcut(np.arange(len(frame)), q=count, labels=False, duplicates="drop") + 1
    overall = float(frame["target"].mean()) if len(frame) else 0
    total_bad = max(float(frame["target"].sum()), 1)
    grouped = frame.groupby("bucket", as_index=False).agg(
        count=("target", "size"),
        bad=("target", "sum"),
        min_probability=("probability", "min"),
        max_probability=("probability", "max"),
    )
    cumulative = 0.0
    rows: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        bad = float(row["bad"])
        cumulative += bad
        rate = bad / float(row["count"])
        rows.append(
            {
                "bucket": int(row["bucket"]),
                "decile": int(row["bucket"]),
                "count": int(row["count"]),
                "bad": int(bad),
                "bad_rate": rate,
                "response_rate": rate,
                "positive_rate": rate,
                "lift": rate / overall if overall else None,
                "cumulative_capture": cumulative / total_bad,
                "cumulative_capture_rate": cumulative / total_bad,
                "min_probability": float(row["min_probability"]),
                "max_probability": float(row["max_probability"]),
            }
        )
    return rows


def calibration_table(
    y_true: np.ndarray, probability: np.ndarray, bins: int = 10
) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"target": y_true, "probability": probability})
    frame["bucket"] = pd.cut(frame["probability"], np.linspace(0, 1, bins + 1), include_lowest=True)
    grouped = frame.groupby("bucket", observed=True).agg(
        count=("target", "size"),
        actual_rate=("target", "mean"),
        predicted_rate=("probability", "mean"),
    )
    return [
        {
            "bucket": str(index),
            "count": int(row["count"]),
            "actual_rate": float(row["actual_rate"]),
            "predicted_rate": float(row["predicted_rate"]),
        }
        for index, row in grouped.iterrows()
    ]


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float | None:
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    if not len(reference) or not len(current):
        return None
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref_counts, _ = np.histogram(reference, edges)
    cur_counts, _ = np.histogram(current, edges)
    ref_share = np.clip(ref_counts / max(ref_counts.sum(), 1), 1e-6, None)
    cur_share = np.clip(cur_counts / max(cur_counts.sum(), 1), 1e-6, None)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


def score_monotonicity(rows: list[dict[str, Any]], rate_key: str = "bad_rate") -> dict[str, Any]:
    values = [row.get(rate_key) for row in rows if row.get(rate_key) is not None]
    if len(values) < 2:
        return {"absolute": False, "violations": None}
    differences = np.diff(values)
    increasing = int(np.sum(differences < -1e-12))
    decreasing = int(np.sum(differences > 1e-12))
    violations = min(increasing, decreasing)
    return {"absolute": violations == 0, "violations": violations}


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
