from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import pandas as pd

from .profiling import ID_PATTERN


@dataclass(frozen=True)
class JoinStep:
    right_asset_id: str
    left_keys: list[str]
    right_keys: list[str]
    how: str = "left"
    expected_cardinality: str = "many_to_one"
    suffix: str = "_right"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommend_keys(left: pd.DataFrame, right: pd.DataFrame, max_keys: int = 3) -> dict[str, Any]:
    common = [column for column in left.columns if column in right.columns]
    scored: list[dict[str, Any]] = []
    for column in common:
        left_non_null = left[column].dropna()
        right_non_null = right[column].dropna()
        if left_non_null.empty or right_non_null.empty:
            continue
        overlap = len(set(left_non_null.astype(str).head(100_000)) & set(right_non_null.astype(str).head(100_000)))
        base = max(1, min(left_non_null.nunique(), right_non_null.nunique()))
        overlap_rate = overlap / base
        name_bonus = 0.35 if ID_PATTERN.search(str(column)) else 0
        right_unique = float(right_non_null.nunique() / len(right_non_null))
        scored.append(
            {
                "left_keys": [str(column)],
                "right_keys": [str(column)],
                "score": round(min(1.0, overlap_rate + name_bonus), 4),
                "overlap_rate": round(overlap_rate, 4),
                "right_unique_rate": round(right_unique, 4),
                "expected_cardinality": "many_to_one" if right_unique >= 0.98 else "many_to_many",
            }
        )
    scored.sort(key=lambda item: (item["expected_cardinality"] == "many_to_one", item["score"]), reverse=True)
    return {"recommendations": scored[:max_keys], "requires_manual": not bool(scored)}


def validate_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_keys: Sequence[str],
    right_keys: Sequence[str],
    target_columns: Sequence[str] = (),
    customer_key: str | None = None,
) -> dict[str, Any]:
    if not left_keys or len(left_keys) != len(right_keys):
        raise ValueError("JOIN_KEYS_INVALID")
    missing_left = [key for key in left_keys if key not in left]
    missing_right = [key for key in right_keys if key not in right]
    if missing_left or missing_right:
        raise ValueError(f"JOIN_KEY_NOT_FOUND: left={missing_left}, right={missing_right}")
    left_duplicates = int(left.duplicated(list(left_keys)).sum())
    right_duplicates = int(right.duplicated(list(right_keys)).sum())
    right_projection = right[list(right_keys)].drop_duplicates()
    matched = left.merge(
        right_projection,
        how="inner",
        left_on=list(left_keys),
        right_on=list(right_keys),
    )
    match_rate = len(matched) / max(len(left), 1)
    issues: list[dict[str, Any]] = []
    if right_duplicates:
        issues.append(
            {
                "code": "RIGHT_KEY_DUPLICATES",
                "severity": "blocking",
                "count": right_duplicates,
                "message": "右表关联键不唯一，直接关联会导致样本膨胀。",
            }
        )
    if match_rate < 0.5:
        issues.append({"code": "LOW_MATCH_RATE", "severity": "warning", "value": match_rate, "message": "关联匹配率低于 50%。"})
    if customer_key and customer_key in left:
        grain = "customer" if left[customer_key].is_unique else "order_or_event"
    else:
        grain = "unknown"
    before_targets = {
        column: left[column].fillna("<MISSING>").astype(str).value_counts().astype(int).to_dict()
        for column in target_columns
        if column in left
    }
    return {
        "left_rows": len(left),
        "right_rows": len(right),
        "left_key_duplicates": left_duplicates,
        "right_key_duplicates": right_duplicates,
        "match_rate": match_rate,
        "grain": grain,
        "target_before": before_targets,
        "issues": issues,
    }


def execute_join(
    base: pd.DataFrame,
    right: pd.DataFrame,
    step: JoinStep,
    target_columns: Sequence[str] = (),
    customer_key: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    validation = validate_join(
        base,
        right,
        step.left_keys,
        step.right_keys,
        target_columns,
        customer_key,
    )
    if any(issue["severity"] == "blocking" for issue in validation["issues"]):
        raise ValueError("JOIN_VALIDATION_BLOCKED")
    joined = base.merge(
        right,
        how=step.how,
        left_on=step.left_keys,
        right_on=step.right_keys,
        suffixes=("", step.suffix),
        validate=step.expected_cardinality,
    )
    inflation = len(joined) / max(len(base), 1)
    target_after = {
        column: joined[column].fillna("<MISSING>").astype(str).value_counts().astype(int).to_dict()
        for column in target_columns
        if column in joined
    }
    if inflation > 1.001:
        raise ValueError("JOIN_SAMPLE_INFLATION")
    if validation["target_before"] != target_after:
        raise ValueError("JOIN_TARGET_DISTRIBUTION_CHANGED")
    lineage = {
        **validation,
        "output_rows": len(joined),
        "output_columns": len(joined.columns),
        "inflation_ratio": inflation,
        "target_after": target_after,
        "step": step.as_dict(),
    }
    return joined, lineage
