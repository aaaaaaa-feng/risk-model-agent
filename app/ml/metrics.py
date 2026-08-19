"""Metrics used by the deterministic binary-classification workflow.

All public functions return plain Python values so their outputs can be written
to JSON without a custom encoder.  Scores are expected to be probabilities for
the configured positive class, where larger values mean greater positive risk.
"""

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)


def _validated_arrays(
    y_true: Sequence[Any], y_probability: Sequence[float]
) -> Tuple[np.ndarray, np.ndarray]:
    """Return validated one-dimensional arrays for binary probability metrics."""

    actual = np.asarray(y_true)
    probability = np.asarray(y_probability, dtype=float)

    if actual.ndim != 1 or probability.ndim != 1:
        raise ValueError("y_true and y_probability must be one-dimensional")
    if actual.size == 0:
        raise ValueError("at least one observation is required")
    if actual.size != probability.size:
        raise ValueError("y_true and y_probability must have the same length")
    if not np.all(np.isfinite(probability)):
        raise ValueError("y_probability must contain only finite values")
    if np.any((probability < 0.0) | (probability > 1.0)):
        raise ValueError("y_probability values must be between 0 and 1")

    try:
        actual_float = actual.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("y_true must contain only binary 0/1 values") from exc

    if not np.all(np.isfinite(actual_float)):
        raise ValueError("y_true must contain only finite binary values")
    unique = set(actual_float.tolist())
    if unique != {0.0, 1.0}:
        raise ValueError("y_true must contain both binary classes 0 and 1")

    return actual_float.astype(int), probability


def _validated_threshold(threshold: float) -> float:
    value = float(threshold)
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError("threshold must be a finite number between 0 and 1")
    return value


def ks_statistic(y_true: Sequence[Any], y_probability: Sequence[float]) -> float:
    """Return the maximum positive-class separation (TPR minus FPR).

    This is the directional KS used for risk scores: a larger probability must
    indicate a greater chance of the configured positive event.
    """

    actual, probability = _validated_arrays(y_true, y_probability)
    false_positive_rate, true_positive_rate, _ = roc_curve(
        actual, probability, drop_intermediate=False
    )
    return float(np.max(true_positive_rate - false_positive_rate))


def best_ks_threshold(y_true: Sequence[Any], y_probability: Sequence[float]) -> float:
    """Return the highest finite probability threshold that maximises KS.

    ``roc_curve`` orders thresholds from high to low.  Choosing the first
    finite maximum therefore makes ties deterministic and selects the more
    conservative (higher) threshold.
    """

    actual, probability = _validated_arrays(y_true, y_probability)
    false_positive_rate, true_positive_rate, thresholds = roc_curve(
        actual, probability, drop_intermediate=False
    )
    finite = np.isfinite(thresholds)
    if not np.any(finite):
        raise ValueError("a finite KS threshold could not be determined")

    finite_thresholds = thresholds[finite]
    separations = (true_positive_rate - false_positive_rate)[finite]
    best_index = int(np.argmax(separations))
    return float(np.clip(finite_thresholds[best_index], 0.0, 1.0))


def evaluate_probabilities(
    y_true: Sequence[Any],
    y_probability: Sequence[float],
    threshold: float = 0.5,
) -> Dict[str, Any]:
    """Evaluate probabilities and thresholded predictions for a binary target."""

    actual, probability = _validated_arrays(y_true, y_probability)
    cutoff = _validated_threshold(threshold)
    predicted = (probability >= cutoff).astype(int)
    matrix = confusion_matrix(actual, predicted, labels=[0, 1])

    return {
        "roc_auc": float(roc_auc_score(actual, probability)),
        "pr_auc": float(average_precision_score(actual, probability)),
        "ks": ks_statistic(actual, probability),
        "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
        "brier_score": float(brier_score_loss(actual, probability)),
        "accuracy": float(accuracy_score(actual, predicted)),
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "recall": float(recall_score(actual, predicted, zero_division=0)),
        "f1": float(f1_score(actual, predicted, zero_division=0)),
        "threshold": cutoff,
        "positive_rate": float(np.mean(actual)),
        "predicted_positive_rate": float(np.mean(predicted)),
        "confusion_matrix": [[int(value) for value in row] for row in matrix],
    }


def lift_table(
    y_true: Sequence[Any],
    y_probability: Sequence[float],
    bins: int = 10,
) -> List[Dict[str, Any]]:
    """Return an aggregate lift table ordered from highest to lowest score.

    Buckets contain nearly equal numbers of observations and are generated from
    a stable score ordering, so ties remain deterministic.  No row-level values
    are returned.
    """

    actual, probability = _validated_arrays(y_true, y_probability)
    if isinstance(bins, bool) or not isinstance(bins, (int, np.integer)):
        raise ValueError("bins must be a positive integer")
    if int(bins) <= 0:
        raise ValueError("bins must be a positive integer")

    bucket_count = min(int(bins), int(actual.size))
    ordered = pd.DataFrame({"actual": actual, "probability": probability})
    ordered = ordered.sort_values("probability", ascending=False, kind="mergesort").reset_index(
        drop=True
    )
    ordered["bucket"] = (
        np.floor(np.arange(len(ordered)) * bucket_count / len(ordered)).astype(int) + 1
    )

    total_positives = int(ordered["actual"].sum())
    overall_rate = float(ordered["actual"].mean())
    cumulative_positives = 0
    cumulative_count = 0
    rows: List[Dict[str, Any]] = []

    for bucket, group in ordered.groupby("bucket", sort=True):
        count = int(len(group))
        positives = int(group["actual"].sum())
        negatives = count - positives
        response_rate = float(positives / count)
        cumulative_positives += positives
        cumulative_count += count

        rows.append(
            {
                "bucket": int(bucket),
                "decile": int(bucket),
                "count": count,
                "positives": positives,
                "negatives": negatives,
                "average_score": float(group["probability"].mean()),
                "minimum_score": float(group["probability"].min()),
                "maximum_score": float(group["probability"].max()),
                "response_rate": response_rate,
                "positive_rate": response_rate,
                "lift": float(response_rate / overall_rate),
                "cumulative_positives": cumulative_positives,
                "cumulative_capture": float(cumulative_positives / total_positives),
                "cumulative_capture_rate": float(cumulative_positives / total_positives),
                "cumulative_lift": float((cumulative_positives / cumulative_count) / overall_rate),
            }
        )

    return rows
