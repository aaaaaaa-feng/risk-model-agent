from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from .profiling import target_summary


def freeze_target_samples(frame: pd.DataFrame, target: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    summary = target_summary(frame, target)
    frozen = frame.loc[summary["valid_mask"]].copy()
    frozen[target] = summary["normalized"].loc[summary["valid_mask"]].astype(int)
    evidence = {
        key: value for key, value in summary.items() if key not in {"valid_mask", "normalized"}
    }
    evidence["excluded_rows"] = len(frame) - len(frozen)
    return frozen, evidence


def _partition_summary(frame: pd.DataFrame, indices: np.ndarray, target: str) -> dict[str, Any]:
    sample = frame.iloc[indices]
    positives = int(sample[target].sum())
    return {
        "rows": len(sample),
        "positive_count": positives,
        "negative_count": len(sample) - positives,
        "bad_rate": positives / len(sample) if len(sample) else None,
    }


def _stratified_group_split(
    frame: pd.DataFrame,
    target: str,
    groups: pd.Series,
    test_size: float,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray]:
    group_stats = (
        pd.DataFrame({"group": groups.astype(str), "target": frame[target].astype(int)})
        .groupby("group", as_index=False)
        .agg(target=("target", "max"))
    )
    stratify = group_stats["target"] if group_stats["target"].nunique() > 1 else None
    train_groups, test_groups = train_test_split(
        group_stats["group"],
        test_size=test_size,
        random_state=random_state,
        stratify=stratify,
    )
    train_set = set(train_groups)
    test_set = set(test_groups)
    group_values = groups.astype(str)
    return (
        np.flatnonzero(group_values.isin(train_set).to_numpy()),
        np.flatnonzero(group_values.isin(test_set).to_numpy()),
    )


def split_dataset(
    frame: pd.DataFrame,
    target: str,
    method: str = "time_holdout",
    time_column: str | None = None,
    customer_key: str | None = None,
    test_size: float = 0.2,
    oot_size: float = 0.2,
    random_state: int = 42,
) -> dict[str, Any]:
    if len(frame) < 30:
        raise ValueError("INSUFFICIENT_SAMPLES_FOR_SPLIT")
    positions = np.arange(len(frame))
    if customer_key and customer_key not in frame:
        raise ValueError("CUSTOMER_KEY_NOT_FOUND")
    groups = frame[customer_key].fillna("<MISSING_CUSTOMER>").astype(str) if customer_key else None
    if method == "time_holdout":
        if not time_column or time_column not in frame:
            raise ValueError("TIME_COLUMN_REQUIRED")
        parsed = pd.to_datetime(frame[time_column], errors="coerce")
        if parsed.notna().mean() < 0.8:
            raise ValueError("TIME_PARSE_RATE_TOO_LOW")
        if groups is not None:
            group_time = pd.DataFrame({"group": groups, "time": parsed}).groupby("group")["time"].max().sort_values()
            cut = max(1, int(np.ceil(len(group_time) * oot_size)))
            oot_groups = set(group_time.tail(cut).index)
            oot_idx = np.flatnonzero(groups.isin(oot_groups).to_numpy())
            development_idx = np.flatnonzero(~groups.isin(oot_groups).to_numpy())
            development = frame.iloc[development_idx]
            dev_groups = groups.iloc[development_idx]
            relative_train, relative_test = _stratified_group_split(
                development, target, dev_groups, test_size, random_state
            )
            train_idx = development_idx[relative_train]
            test_idx = development_idx[relative_test]
        else:
            ordered = positions[np.argsort(parsed.fillna(pd.Timestamp.min).to_numpy())]
            cut = max(1, int(np.ceil(len(frame) * oot_size)))
            oot_idx = ordered[-cut:]
            development_idx = ordered[:-cut]
            train_idx, test_idx = train_test_split(
                development_idx,
                test_size=test_size,
                random_state=random_state,
                stratify=frame.iloc[development_idx][target],
            )
    elif method == "random_stratified":
        oot_idx = np.array([], dtype=int)
        if groups is not None:
            train_idx, test_idx = _stratified_group_split(
                frame, target, groups, test_size, random_state
            )
        else:
            train_idx, test_idx = train_test_split(
                positions,
                test_size=test_size,
                random_state=random_state,
                stratify=frame[target],
            )
    else:
        raise ValueError("UNSUPPORTED_SPLIT_METHOD")
    partitions = {
        "train": np.sort(np.asarray(train_idx, dtype=int)),
        "test": np.sort(np.asarray(test_idx, dtype=int)),
        "oot": np.sort(np.asarray(oot_idx, dtype=int)),
    }
    assert_split_integrity(frame, partitions, customer_key)
    return {
        "method": method,
        "time_column": time_column,
        "customer_key": customer_key,
        "random_state": random_state,
        "indices": {key: value.tolist() for key, value in partitions.items()},
        "summary": {
            key: _partition_summary(frame, value, target) for key, value in partitions.items()
        },
        "fit_scope": "train_only",
        "oot_locked": bool(len(oot_idx)),
    }


def assert_split_integrity(
    frame: pd.DataFrame, partitions: dict[str, np.ndarray], customer_key: str | None
) -> None:
    names = list(partitions)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            overlap = set(partitions[left_name]) & set(partitions[right_name])
            if overlap:
                raise ValueError(f"SPLIT_ROW_OVERLAP: {left_name}/{right_name}")
    if customer_key:
        for index, left_name in enumerate(names):
            left_groups = set(frame.iloc[partitions[left_name]][customer_key].dropna().astype(str))
            for right_name in names[index + 1 :]:
                right_groups = set(frame.iloc[partitions[right_name]][customer_key].dropna().astype(str))
                if left_groups & right_groups:
                    raise ValueError(f"SPLIT_CUSTOMER_OVERLAP: {left_name}/{right_name}")
