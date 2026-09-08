from __future__ import annotations

import numpy as np
import pandas as pd

from app.workers.modeling import train_candidates


def test_controlled_train_cv_search_is_recorded_without_oot_selection():
    frame = pd.DataFrame(
        {
            "x1": np.linspace(-2, 2, 240),
            "x2": np.sin(np.linspace(0, 8, 240)),
            "Y": (np.arange(240) % 3 == 0).astype(int),
        }
    )
    split = {
        "indices": {
            "train": np.arange(0, 160),
            "test": np.arange(160, 200),
            "oot": np.arange(200, 240),
        }
    }
    result, _ = train_candidates(
        frame,
        "Y",
        ["x1", "x2"],
        split,
        models=["regularized_logistic"],
        search_budget=2,
    )
    candidate = result["candidates"][0]
    assert result["search_budget"] == 2
    assert candidate["search"]["enabled"] is True
    assert len(candidate["search"]["trials"]) == 2
    assert result["oot_used_for_selection"] is False


def test_development_training_never_predicts_holdout(monkeypatch):
    from app.workers.modeling import ModelBundle, evaluate_final_holdout

    frame = pd.DataFrame({"x": np.arange(100), "Y": np.arange(100) % 2})
    split = {
        "indices": {
            "train": list(range(60)),
            "test": list(range(60, 80)),
            "oot": list(range(80, 100)),
        }
    }
    original = ModelBundle.predict_proba
    accessed = []

    def observe(self, sample):
        accessed.extend(sample.index.tolist())
        return original(self, sample)

    monkeypatch.setattr(ModelBundle, "predict_proba", observe)
    result, bundles = train_candidates(
        frame, "Y", ["x"], split, models=["dummy"], evaluate_oot=False
    )
    assert not set(range(80, 100)).intersection(accessed)
    assert result["champion_metrics"]["oot"] is None
    evaluate_final_holdout(frame, "Y", split, result, bundles["dummy"])
    assert set(range(80, 100)).issubset(accessed)
    assert result["final_holdout_evaluated"] is True
    import pytest

    with pytest.raises(ValueError, match="FINAL_HOLDOUT_ALREADY_EVALUATED"):
        evaluate_final_holdout(frame, "Y", split, result, bundles["dummy"])
