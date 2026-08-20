from __future__ import annotations

import math
import re
from typing import Any, Sequence

import numpy as np
import pandas as pd

from app.core.security import is_pii_column

from .io import recommended_batches


ID_PATTERN = re.compile(r"(^id$|_id$|id_|uuid|订单号|客户号|流水号|主键)", re.IGNORECASE)
TIME_PATTERN = re.compile(
    r"((?:^|_)(?:date|time|month|day|datetime|timestamp)(?:$|_)|日期|时间|月份)",
    re.IGNORECASE,
)
LEAKAGE_PATTERN = re.compile(
    r"(post_|after_|贷后|催收|结清|逾期结果|最终状态|repay|collection|write[_-]?off|charge[_-]?off)",
    re.IGNORECASE,
)
HISTORICAL_RISK_PATTERN = re.compile(
    r"^(prior_|historical_|pre_application_).*(delinquen|overdue|past_due)", re.IGNORECASE
)


def infer_type(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "empty"
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    sample = non_null.astype(str).head(200)
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    if parsed.notna().mean() >= 0.9 and TIME_PATTERN.search(str(series.name)):
        return "datetime"
    unique = int(non_null.nunique(dropna=True))
    if unique <= min(100, max(20, int(len(non_null) * 0.05))):
        return "categorical"
    return "text"


def target_candidate(series: pd.Series) -> dict[str, Any] | None:
    values = series.dropna()
    if values.empty:
        return None
    normalized = {str(item).strip().lower() for item in values.unique()[:20]}
    allowed = {"0", "1", "-1", "0.0", "1.0", "-1.0", "true", "false"}
    if not normalized.issubset(allowed) or not ({"0", "1"} <= normalized or {"0.0", "1.0"} <= normalized):
        return None
    counts = values.astype(str).value_counts().to_dict()
    return {"values": sorted(normalized), "counts": counts, "missing": int(series.isna().sum())}


def parse_data_dictionary(frame: pd.DataFrame) -> dict[str, Any]:
    aliases = {
        "field": {"field", "column", "name", "字段", "字段名", "变量名"},
        "description": {"description", "desc", "meaning", "含义", "字段含义", "变量含义"},
        "missing_codes": {"missing", "missing_codes", "缺失值", "缺失码"},
        "role": {"role", "字段角色", "用途"},
    }
    mapping: dict[str, str] = {}
    for column in frame.columns:
        lowered = str(column).strip().lower()
        for key, candidates in aliases.items():
            if lowered in candidates:
                mapping[key] = str(column)
    if "field" not in mapping:
        raise ValueError("DATA_DICTIONARY_FIELD_COLUMN_REQUIRED")
    fields: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        name = str(row.get(mapping["field"], "")).strip()
        if not name or name.lower() == "nan":
            continue
        missing_codes: list[str] = []
        if "missing_codes" in mapping and pd.notna(row.get(mapping["missing_codes"])):
            missing_codes = [
                item.strip() for item in re.split(r"[,，;；|]", str(row[mapping["missing_codes"]])) if item.strip()
            ]
        fields[name] = {
            "description": str(row.get(mapping.get("description", ""), "") or "").strip(),
            "role": str(row.get(mapping.get("role", ""), "") or "").strip(),
            "missing_codes": missing_codes,
        }
    return {"fields": fields, "mapping": mapping, "rows": len(fields)}


def profile_frame(
    frame: pd.DataFrame,
    dictionary: dict[str, Any] | None = None,
    memory_budget_mb: int = 1536,
) -> dict[str, Any]:
    dictionary_fields = (dictionary or {}).get("fields", {})
    details: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for batch in recommended_batches(frame, memory_budget_mb):
        for column in batch:
            series = frame[column]
            info = dictionary_fields.get(str(column), {})
            inferred = infer_type(series)
            candidate = target_candidate(series)
            item = {
                "name": str(column),
                "type": inferred,
                "missing_count": int(series.isna().sum()),
                "missing_rate": float(series.isna().mean()),
                "unique_count": int(series.nunique(dropna=True)),
                "pii": is_pii_column(str(column)),
                "id_candidate": bool(ID_PATTERN.search(str(column))),
                "time_candidate": inferred == "datetime" or bool(TIME_PATTERN.search(str(column))),
                "target_candidate": candidate is not None,
                "dictionary": info,
            }
            if inferred == "numeric" and series.notna().any():
                numeric = pd.to_numeric(series, errors="coerce")
                item["summary"] = {
                    "min": finite_or_none(numeric.min()),
                    "p25": finite_or_none(numeric.quantile(0.25)),
                    "median": finite_or_none(numeric.median()),
                    "p75": finite_or_none(numeric.quantile(0.75)),
                    "max": finite_or_none(numeric.max()),
                    "mean": finite_or_none(numeric.mean()),
                }
            else:
                item["top_values"] = [
                    {"value": str(key), "count": int(value)}
                    for key, value in series.fillna("<MISSING>").astype(str).value_counts().head(10).items()
                ]
            details.append(item)
            if candidate:
                candidates.append({"column": str(column), **candidate})
    return {
        "rows": int(len(frame)),
        "columns": int(len(frame.columns)),
        "duplicate_rows": int(frame.duplicated().sum()),
        "columns_detail": details,
        "target_candidates": candidates,
        "binary_candidates": [item["column"] for item in candidates],
        "memory_bytes": int(frame.memory_usage(deep=True).sum()),
    }


def target_summary(frame: pd.DataFrame, target: str) -> dict[str, Any]:
    if target not in frame:
        raise ValueError("TARGET_NOT_FOUND")
    series = frame[target]
    normalized = series.map(normalize_binary)
    valid = normalized.isin([0, 1])
    counts = normalized[valid].value_counts().to_dict()
    invalid = int((series.notna() & ~valid).sum())
    missing = int(series.isna().sum())
    positives = int(counts.get(1, 0))
    negatives = int(counts.get(0, 0))
    total = positives + negatives
    issues: list[dict[str, Any]] = []
    if not positives or not negatives:
        issues.append({"code": "TARGET_SINGLE_CLASS", "severity": "blocking", "message": "Y 有效样本必须同时包含 0 和 1。"})
    if total < 100:
        issues.append({"code": "TARGET_TOO_SMALL", "severity": "warning", "message": "有效 Y 样本少于 100，模型结果仅适合流程验证。"})
    return {
        "target": target,
        "valid_count": total,
        "positive_count": positives,
        "negative_count": negatives,
        "bad_rate": positives / total if total else None,
        "invalid_count": invalid,
        "missing_count": missing,
        "valid_mask": valid,
        "normalized": normalized,
        "issues": issues,
    }


def normalize_binary(value: Any) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    text = str(value).strip().lower()
    if text in {"1", "1.0", "true", "bad", "yes"}:
        return 1.0
    if text in {"0", "0.0", "false", "good", "no"}:
        return 0.0
    return np.nan


def diagnose_frame(frame: pd.DataFrame, target: str, time_column: str | None = None) -> dict[str, Any]:
    profile = profile_frame(frame)
    target_info = target_summary(frame, target)
    issues = list(target_info["issues"])
    if profile["duplicate_rows"]:
        issues.append(
            {
                "code": "DUPLICATE_ROWS",
                "severity": "warning",
                "count": profile["duplicate_rows"],
                "message": "发现完全重复行，需要确认去重粒度。",
            }
        )
    if time_column:
        if time_column not in frame:
            issues.append({"code": "TIME_COLUMN_NOT_FOUND", "severity": "blocking", "message": "指定时间字段不存在。"})
        else:
            parsed = pd.to_datetime(frame[time_column], errors="coerce")
            if parsed.notna().mean() < 0.8:
                issues.append({"code": "TIME_PARSE_LOW", "severity": "blocking", "message": "时间字段可解析比例低于 80%。"})
    for item in profile["columns_detail"]:
        if item["missing_rate"] >= 0.95:
            issues.append({"code": "ALMOST_EMPTY_COLUMN", "severity": "warning", "columns": [item["name"]], "message": "字段缺失率达到 95%。"})
    return {"profile": profile, "target": {key: value for key, value in target_info.items() if key not in {"valid_mask", "normalized"}}, "issues": issues}


def cleaning_plan(frame: pd.DataFrame, target: str, time_column: str | None = None) -> dict[str, Any]:
    diagnostics = diagnose_frame(frame, target, time_column)
    actions: list[dict[str, Any]] = []
    if diagnostics["profile"]["duplicate_rows"]:
        actions.append({"id": "drop_exact_duplicates", "kind": "drop_duplicates", "recommended": True, "requires_confirmation": True})
    for item in diagnostics["profile"]["columns_detail"]:
        if item["missing_rate"] == 1:
            actions.append({"id": f"drop_empty:{item['name']}", "kind": "drop_columns", "columns": [item["name"]], "recommended": True, "requires_confirmation": True})
        codes = item.get("dictionary", {}).get("missing_codes") or []
        if codes:
            actions.append({"id": f"replace_missing:{item['name']}", "kind": "replace_missing_codes", "columns": [item["name"]], "codes": codes, "recommended": True, "requires_confirmation": True})
    return {"actions": actions, "diagnostics": diagnostics}


def apply_cleaning(frame: pd.DataFrame, actions: Sequence[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    cleaned = frame.copy()
    applied: list[dict[str, Any]] = []
    for action in actions:
        kind = action.get("kind")
        if kind == "drop_duplicates":
            before = len(cleaned)
            cleaned = cleaned.drop_duplicates().reset_index(drop=True)
            applied.append({**action, "removed_rows": before - len(cleaned)})
        elif kind == "drop_columns":
            columns = [column for column in action.get("columns", []) if column in cleaned]
            cleaned = cleaned.drop(columns=columns)
            applied.append({**action, "columns": columns})
        elif kind == "replace_missing_codes":
            columns = [column for column in action.get("columns", []) if column in cleaned]
            codes = {str(code) for code in action.get("codes", [])}
            replaced = 0
            for column in columns:
                mask = cleaned[column].astype(str).isin(codes)
                replaced += int(mask.sum())
                cleaned.loc[mask, column] = np.nan
            applied.append({**action, "replaced_cells": replaced})
        elif kind == "clip_numeric":
            column = action.get("column")
            if column in cleaned:
                lower = float(action["lower"])
                upper = float(action["upper"])
                cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce").clip(lower, upper)
                applied.append(dict(action))
        else:
            raise ValueError(f"UNSUPPORTED_CLEANING_ACTION: {kind}")
    return cleaned, {"applied": applied, "rows": len(cleaned), "columns": len(cleaned.columns)}


def leakage_flags(columns: Sequence[str], target: str) -> dict[str, list[str]]:
    blocked: list[str] = []
    review: list[str] = []
    for column in columns:
        if column == target:
            continue
        if HISTORICAL_RISK_PATTERN.search(column):
            review.append(column)
        elif LEAKAGE_PATTERN.search(column):
            blocked.append(column)
    return {"blocked": blocked, "historical_review": review}


def finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
