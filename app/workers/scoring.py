from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def probability_to_score(
    probability: np.ndarray,
    minimum: float = 300,
    maximum: float = 900,
    base_score: float = 600,
    base_odds: float = 20,
    pdo: float = 50,
) -> dict[str, Any]:
    probability = np.clip(np.asarray(probability, dtype=float), 1e-8, 1 - 1e-8)
    odds = (1 - probability) / probability
    factor = pdo / math.log(2)
    raw = base_score + factor * np.log(odds / base_odds)
    score = np.clip(raw, minimum, maximum)
    return {
        "raw_score": raw,
        "score": score,
        "floor_rate": float(np.mean(raw < minimum)),
        "cap_rate": float(np.mean(raw > maximum)),
        "config": {
            "minimum": minimum,
            "maximum": maximum,
            "base_score": base_score,
            "base_odds": base_odds,
            "pdo": pdo,
            "direction": "higher_score_lower_risk",
        },
    }


def score_column_name(model_name: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in model_name)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return f"{cleaned or 'risk_model'}_score"


def append_scores(
    frame: pd.DataFrame,
    probability: np.ndarray,
    model_name: str,
    score_config: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = score_config or {}
    transformed = probability_to_score(probability, **config)
    output = frame.copy()
    column = score_column_name(model_name)
    output[column] = np.round(transformed["score"]).astype(int)
    output[f"{column}_raw"] = transformed["raw_score"]
    output[f"{column}_bad_probability"] = np.asarray(probability, dtype=float)
    return output, {
        "score_column": column,
        "rows": len(output),
        "floor_rate": transformed["floor_rate"],
        "cap_rate": transformed["cap_rate"],
        "config": transformed["config"],
    }
