from __future__ import annotations

import hashlib
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
    if not 0 < test_size < 0.5:
        raise ValueError("TEST_SIZE_INVALID")
    if method == "time_holdout" and not 0 < oot_size < 0.5:
        raise ValueError("OOT_SIZE_INVALID")
    positions = np.arange(len(frame))
    if customer_key and customer_key not in frame:
        raise ValueError("CUSTOMER_KEY_NOT_FOUND")
    groups = frame[customer_key].fillna("<MISSING_CUSTOMER>").astype(str) if customer_key else None
    excluded_idx = np.array([], dtype=int)
    exclusion_reasons: dict[str, int] = {}
    cutoff: pd.Timestamp | None = None
    if method == "time_holdout":
        if not time_column or time_column not in frame:
            raise ValueError("TIME_COLUMN_REQUIRED")
        parsed = pd.to_datetime(frame[time_column], errors="coerce")
        if parsed.notna().mean() < 0.8:
            raise ValueError("TIME_PARSE_RATE_TOO_LOW")
        valid_time_positions = positions[parsed.notna().to_numpy()]
        if len(valid_time_positions) < 3:
            raise ValueError("TIME_VALID_SAMPLE_TOO_SMALL")
        ordered_valid = valid_time_positions[
            np.argsort(parsed.iloc[valid_time_positions].to_numpy())
        ]
        requested_oot_rows = max(1, int(np.ceil(len(ordered_valid) * oot_size)))
        boundary_position = max(0, len(ordered_valid) - requested_oot_rows)
        cutoff = pd.Timestamp(parsed.iloc[ordered_valid[boundary_position]])
        before_cutoff = parsed < cutoff
        on_or_after_cutoff = parsed >= cutoff
        missing_time_idx = np.flatnonzero(parsed.isna().to_numpy())
        if groups is not None:
            group_boundaries = (
                pd.DataFrame(
                    {"group": groups, "before": before_cutoff, "after": on_or_after_cutoff}
                )
                .groupby("group", as_index=True)
                .agg(before=("before", "any"), after=("after", "any"))
            )
            cross_groups = set(
                group_boundaries.index[group_boundaries["before"] & group_boundaries["after"]]
            )
            development_groups = set(
                group_boundaries.index[group_boundaries["before"] & ~group_boundaries["after"]]
            )
            oot_groups = set(
                group_boundaries.index[~group_boundaries["before"] & group_boundaries["after"]]
            )
            development_idx = np.flatnonzero(
                (groups.isin(development_groups) & before_cutoff).to_numpy()
            )
            oot_idx = np.flatnonzero((groups.isin(oot_groups) & on_or_after_cutoff).to_numpy())
            cross_idx = np.flatnonzero(groups.isin(cross_groups).to_numpy())
            excluded_idx = np.unique(np.concatenate([missing_time_idx, cross_idx])).astype(int)
            exclusion_reasons = {
                "missing_or_invalid_time": int(len(missing_time_idx)),
                "cross_boundary_customer": int(len(cross_idx)),
            }
            if len(development_idx) < 20 or len(oot_idx) < 2:
                raise ValueError("STRICT_OOT_INSUFFICIENT_AFTER_CUSTOMER_ISOLATION")
            development = frame.iloc[development_idx]
            dev_groups = groups.iloc[development_idx]
            relative_train, relative_test = _stratified_group_split(
                development, target, dev_groups, test_size, random_state
            )
            train_idx = development_idx[relative_train]
            test_idx = development_idx[relative_test]
        else:
            oot_idx = np.flatnonzero(on_or_after_cutoff.to_numpy())
            development_idx = np.flatnonzero(before_cutoff.to_numpy())
            excluded_idx = missing_time_idx.astype(int)
            exclusion_reasons = {"missing_or_invalid_time": int(len(missing_time_idx))}
            if len(development_idx) < 20 or len(oot_idx) < 2:
                raise ValueError("STRICT_OOT_INSUFFICIENT_SAMPLES")
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
    assert_split_integrity(
        frame,
        partitions,
        customer_key,
        excluded=np.sort(np.asarray(excluded_idx, dtype=int)),
        time_column=time_column if method == "time_holdout" else None,
    )
    return {
        "method": method,
        "time_column": time_column,
        "customer_key": customer_key,
        "random_state": random_state,
        "indices": {key: value.tolist() for key, value in partitions.items()},
        "excluded_indices": np.sort(np.asarray(excluded_idx, dtype=int)).tolist(),
        "exclusions": exclusion_reasons,
        "excluded_index_sha256": hashlib.sha256(
            ",".join(
                str(value) for value in np.sort(np.asarray(excluded_idx, dtype=int)).tolist()
            ).encode("ascii")
        ).hexdigest(),
        "summary": {
            key: _partition_summary(frame, value, target) for key, value in partitions.items()
        },
        "fit_scope": "train_only",
        "oot_locked": bool(len(oot_idx)),
        "strict_time_boundary": bool(method == "time_holdout"),
        "time_cutoff": cutoff.isoformat() if cutoff is not None else None,
    }


def assert_split_integrity(
    frame: pd.DataFrame,
    partitions: dict[str, np.ndarray],
    customer_key: str | None,
    *,
    excluded: np.ndarray | None = None,
    time_column: str | None = None,
) -> None:
    excluded = np.asarray(excluded if excluded is not None else [], dtype=int)
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
                right_groups = set(
                    frame.iloc[partitions[right_name]][customer_key].dropna().astype(str)
                )
                if left_groups & right_groups:
                    raise ValueError(f"SPLIT_CUSTOMER_OVERLAP: {left_name}/{right_name}")
    assigned = set(excluded.tolist())
    for values in partitions.values():
        if assigned & set(values.tolist()):
            raise ValueError("SPLIT_EXCLUDED_ROW_ASSIGNED")
        assigned.update(values.tolist())
    if assigned != set(range(len(frame))):
        raise ValueError("SPLIT_ROWS_NOT_ACCOUNTED_FOR")
    if time_column and len(partitions.get("oot", [])):
        development = np.concatenate(
            [
                partitions.get("train", np.array([], dtype=int)),
                partitions.get("test", np.array([], dtype=int)),
            ]
        )
        parsed = pd.to_datetime(frame[time_column], errors="coerce")
        development_max = parsed.iloc[development].max()
        oot_min = parsed.iloc[partitions["oot"]].min()
        if pd.isna(development_max) or pd.isna(oot_min) or not development_max < oot_min:
            raise ValueError("STRICT_OOT_TIME_BOUNDARY_VIOLATION")
