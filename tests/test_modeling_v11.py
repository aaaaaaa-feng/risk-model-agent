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
