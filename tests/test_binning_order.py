from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.workers.binning import apply_manual_binning, fit_numeric_bins


def _frame(bads: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": np.repeat([0, 5, 50, 500], 10),
            "Y": [label for bad in bads for label in [1] * bad + [0] * (10 - bad)],
        }
    )


def test_text_order_cannot_turn_non_monotonic_numeric_bins_into_a_pass():
    # Numeric rates: .1, .4, .2, .3. Lexical interval order makes them .1, .2, .3, .4.
    with pytest.raises(ValueError, match="MANUAL_BIN_NOT_MONOTONIC"):
        apply_manual_binning(
            {"specs": {}},
            _frame([1, 4, 2, 3]),
            "Y",
            "x",
            {"kind": "numeric", "edges": [2, 10, 100]},
        )


def test_numeric_order_accepts_a_real_monotonic_manual_plan():
    result = apply_manual_binning(
        {"specs": {}},
        _frame([1, 2, 3, 4]),
        "Y",
        "x",
        {"kind": "numeric", "edges": [2, 10, 100]},
    )["specs"]["x"]
    assert [row["bad_rate"] for row in result["table"]] == [0.1, 0.2, 0.3, 0.4]
    assert result["monotonic"] is True


def test_automatic_numeric_bins_follow_actual_intervals():
    rng = np.random.default_rng(731)
    x = pd.Series(np.linspace(-100, 10000, 2000))
    y = pd.Series((rng.random(len(x)) < np.linspace(0.04, 0.7, len(x))).astype(int))
    spec = fit_numeric_bins(x, y)
    rows = [row for row in spec["table"] if row["bin"] != "<MISSING>"]
    lower = [float(row["bin"].split(",")[0][1:]) for row in rows]
    assert lower == sorted(lower)
    rates = np.diff([row["bad_rate"] for row in rows])
    assert np.all(rates >= -1e-12) or np.all(rates <= 1e-12)


def test_business_exception_does_not_hide_non_monotonicity():
    result = apply_manual_binning(
        {"specs": {}},
        _frame([1, 4, 2, 3]),
        "Y",
        "x",
        {
            "kind": "numeric",
            "edges": [2, 10, 100],
            "business_exception": "教学测试：保留此分箱用于观察非单调分布",
        },
    )["specs"]["x"]
    assert result["monotonic"] is False
    assert result["exception_status"] == "accepted_with_business_exception"
    for suggestion in result["merge_suggestions"]:
        ordered = [row["bin"] for row in result["table"]]
        assert ordered.index(suggestion["right_bin"]) == ordered.index(suggestion["left_bin"]) + 1
