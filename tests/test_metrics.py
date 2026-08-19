import math

import pytest

from app.ml.metrics import (
    best_ks_threshold,
    evaluate_probabilities,
    ks_statistic,
    lift_table,
)


def test_ks_statistic_and_tie_breaking_threshold_are_deterministic():
    actual = [0, 0, 1, 1]
    probability = [0.1, 0.4, 0.35, 0.8]

    assert ks_statistic(actual, probability) == pytest.approx(0.5)
    # Both 0.8 and 0.35 reach KS=0.5.  The higher threshold wins the tie.
    assert best_ks_threshold(actual, probability) == pytest.approx(0.8)


def test_evaluate_probabilities_returns_plain_metrics_and_confusion_matrix():
    metrics = evaluate_probabilities([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8], threshold=0.5)

    assert metrics["roc_auc"] == pytest.approx(0.75)
    assert metrics["confusion_matrix"] == [[2, 0], [1, 1]]
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(0.5)
    assert metrics["threshold"] == pytest.approx(0.5)
    assert all(
        not isinstance(value, float) or math.isfinite(value)
        for key, value in metrics.items()
        if key != "confusion_matrix"
    )


def test_lift_table_is_aggregate_complete_and_score_ordered():
    rows = lift_table(
        [1, 0, 1, 0, 1, 0],
        [0.95, 0.85, 0.75, 0.65, 0.55, 0.45],
        bins=3,
    )

    assert len(rows) == 3
    assert sum(row["count"] for row in rows) == 6
    assert sum(row["positives"] for row in rows) == 3
    assert rows[0]["maximum_score"] >= rows[-1]["maximum_score"]
    assert rows[-1]["cumulative_capture"] == pytest.approx(1.0)
    assert rows[-1]["cumulative_lift"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "actual, probability",
    [
        ([0, 0], [0.1, 0.2]),
        ([0, 1], [0.1]),
        ([0, 1], [0.1, 1.1]),
        ([0, 1], [0.1, float("nan")]),
    ],
)
def test_metrics_reject_invalid_binary_probability_inputs(actual, probability):
    with pytest.raises(ValueError):
        ks_statistic(actual, probability)
