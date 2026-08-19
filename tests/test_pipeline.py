import json

import joblib
import numpy as np
import pandas as pd
import pytest

from app.ml.pipeline import train_candidates


def _stratified_frame(row_count=240):
    random = np.random.RandomState(7)
    age = random.normal(40, 11, row_count)
    income = random.lognormal(10.5, 0.4, row_count)
    city = random.choice(["shanghai", "hangzhou", "suzhou"], row_count)
    signal = (age - 40) * 0.08 - (income - income.mean()) / income.std()
    signal += (city == "suzhou") * 0.8 + random.normal(0, 0.8, row_count)
    target = (signal > np.median(signal)).astype(int)
    frame = pd.DataFrame({"age": age, "income": income, "city": city, "bad_flag": target})
    frame.loc[::19, "income"] = np.nan
    frame.loc[::23, "city"] = None
    return frame


def _plan(candidates=None):
    return {
        "target": {"column": "bad_flag", "positive_label": 1},
        "features": {
            "included_columns": ["age", "income", "city"],
            "column_types": {
                "age": "numeric",
                "income": "numeric",
                "city": "categorical",
            },
        },
        "split": {
            "method": "stratified_random",
            "test_size": 0.2,
            "random_state": 42,
            "time_column": None,
        },
        "candidates": candidates
        or [
            "dummy",
            "logistic_regression",
            "random_forest",
        ],
    }


def test_train_candidates_returns_json_safe_results_and_fitted_model(tmp_path):
    frame = _stratified_frame()
    result, model = train_candidates(frame, _plan())

    json.dumps(result, allow_nan=False)
    assert set(result) == {
        "candidate_comparison",
        "champion",
        "holdout_metrics",
        "threshold",
        "feature_importance",
        "reproducibility",
        "limitations",
    }
    assert len(result["candidate_comparison"]) == 3
    assert all(
        item["roc_auc"] == item["oof_metrics"]["roc_auc"] for item in result["candidate_comparison"]
    )
    assert result["champion"]["name"] in {
        "dummy",
        "logistic_regression",
        "random_forest",
    }
    assert result["threshold"]["source"] == "training_partition_oof_only"
    assert result["reproducibility"]["train_rows"] == 192
    assert result["reproducibility"]["holdout_rows"] == 48
    assert len(result["holdout_metrics"]["lift_table"]) == 10
    assert hasattr(model, "predict_proba")

    model_path = tmp_path / "champion.joblib"
    joblib.dump(model, model_path)
    restored = joblib.load(model_path)
    raw_input = frame[["age", "income", "city"]].head(4).copy()
    raw_input["income"] = raw_input["income"].astype(object)
    raw_input.loc[1, "income"] = "5000.25"
    probabilities = restored.predict_proba(raw_input)
    assert probabilities.shape == (4, 2)


def test_time_holdout_tolerates_category_seen_only_in_holdout():
    row_count = 90
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range("2025-01-01", periods=row_count, freq="D"),
            "amount": np.linspace(10.0, 100.0, row_count),
            "channel": ["web" if index % 2 else "app" for index in range(row_count)],
            "bad_flag": [index % 2 for index in range(row_count)],
        }
    )
    frame.loc[72:, "channel"] = "holdout_only"
    plan = {
        "target": {"column": "bad_flag", "positive_label": 1},
        "features": {
            "included_columns": ["amount", "channel"],
            "column_types": {"amount": "numeric", "channel": "categorical"},
        },
        "split": {
            "method": "time_holdout",
            "test_size": 0.2,
            "random_state": 42,
            "time_column": "event_time",
        },
        "candidates": ["logistic_regression"],
    }

    result, model = train_candidates(frame.sample(frac=1, random_state=3), plan)

    assert result["champion"]["name"] == "logistic_regression"
    assert result["reproducibility"]["split_method"] == "time_holdout"
    assert 0.0 <= result["holdout_metrics"]["roc_auc"] <= 1.0
    assert hasattr(model, "classes_")
    categorical_pipeline = model.named_steps["preprocessor"].named_transformers_["categorical"]
    learned_categories = categorical_pipeline.named_steps["one_hot"].categories_[0]
    assert "holdout_only" not in set(learned_categories)


def test_threshold_is_computed_once_from_champion_oof(monkeypatch):
    import app.ml.pipeline as pipeline_module

    calls = []

    def fixed_threshold(actual, probability):
        calls.append((len(actual), len(probability)))
        return 0.37

    monkeypatch.setattr(pipeline_module, "best_ks_threshold", fixed_threshold)
    result, _ = pipeline_module.train_candidates(
        _stratified_frame(), _plan(["dummy", "logistic_regression"])
    )

    assert len(calls) == 1
    assert result["threshold"]["value"] == pytest.approx(0.37)
    assert result["holdout_metrics"]["threshold"] == pytest.approx(0.37)


def test_training_rejects_non_binary_target():
    frame = _stratified_frame()
    frame.loc[0, "bad_flag"] = 2

    with pytest.raises(ValueError, match="exactly two"):
        train_candidates(frame, _plan(["logistic_regression"]))
