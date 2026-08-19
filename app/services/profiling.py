"""Bounded CSV loading and privacy-conscious dataframe profiling."""

import hashlib
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from pandas.api import types as ptypes

from app.domain import DomainError

HIGH_MISSING_RATE = 0.50
HIGH_CARDINALITY_MIN = 50
HIGH_CARDINALITY_RATE = 0.50

_SENSITIVE_TOKENS = (
    "id_card",
    "identity",
    "national_id",
    "phone",
    "mobile",
    "email",
    "address",
    "bank_card",
    "bankcard",
    "account_no",
    "customer_name",
    "full_name",
    "gender",
    "sex",
    "race",
    "ethnicity",
    "religion",
    "disability",
    "marital",
    "nationality",
    "身份证",
    "手机号",
    "电话",
    "邮箱",
    "地址",
    "姓名",
    "银行卡",
    "账号",
    "性别",
    "民族",
    "宗教",
    "残疾",
    "婚姻",
    "国籍",
    "政治面貌",
)
_ID_TOKENS = (
    "_id",
    "id_",
    "identifier",
    "application_no",
    "apply_no",
    "customer_no",
    "user_no",
    "serial_no",
    "uuid",
    "申请号",
    "客户号",
    "用户号",
    "流水号",
    "编号",
)
_DATE_TOKENS = (
    "date",
    "time",
    "timestamp",
    "_dt",
    "日期",
    "时间",
)


def load_csv(path: Path, max_rows: int = 50000) -> pd.DataFrame:
    """Load at most ``max_rows`` records and preserve truncation provenance."""

    source = Path(path).expanduser().resolve()
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows <= 0:
        raise DomainError(
            400,
            "INVALID_MAX_ROWS",
            "max_rows must be a positive integer.",
        )
    if source.suffix.lower() != ".csv":
        raise DomainError(415, "UNSUPPORTED_DATASET_TYPE", "Only CSV is supported.")
    if not source.is_file():
        raise DomainError(
            404,
            "DATASET_NOT_FOUND",
            "The CSV dataset does not exist.",
            {"path": str(source)},
        )

    last_unicode_error = None
    dataframe = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            dataframe = pd.read_csv(
                source,
                nrows=max_rows + 1,
                encoding=encoding,
                low_memory=False,
            )
            break
        except UnicodeDecodeError as exc:
            last_unicode_error = exc
        except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError) as exc:
            raise DomainError(
                422,
                "CSV_PARSE_FAILED",
                "The CSV could not be parsed into a tabular dataset.",
                {"reason": str(exc)},
            ) from exc
    if dataframe is None:
        raise DomainError(
            422,
            "CSV_ENCODING_UNSUPPORTED",
            "The CSV encoding is not supported; use UTF-8 or GB18030.",
        ) from last_unicode_error
    if len(dataframe.columns) == 0:
        raise DomainError(422, "CSV_HAS_NO_COLUMNS", "The CSV has no columns.")

    truncated = len(dataframe) > max_rows
    if truncated:
        dataframe = dataframe.iloc[:max_rows].copy()
    dataframe.attrs.update(
        {
            "source_path": str(source),
            "source_sha256": _file_sha256(source),
            "max_rows": max_rows,
            "truncated": truncated,
        }
    )
    return dataframe


def profile_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
    """Return aggregate-only profiling metadata; never emit row samples."""

    if not isinstance(df, pd.DataFrame):
        raise DomainError(400, "INVALID_DATAFRAME", "Expected a pandas DataFrame.")
    if len(df.columns) == 0:
        raise DomainError(422, "DATAFRAME_HAS_NO_COLUMNS", "Dataset has no columns.")

    row_count = int(len(df))
    columns: Dict[str, Dict[str, Any]] = {}
    binary_candidates: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    for raw_name in df.columns:
        name = str(raw_name)
        series = df[raw_name]
        non_null = series.dropna()
        non_null_count = int(non_null.shape[0])
        missing_count = row_count - non_null_count
        missing_rate = float(missing_count / row_count) if row_count else 0.0
        unique_count = int(non_null.nunique(dropna=True)) if non_null_count else 0
        unique_rate = float(unique_count / non_null_count) if non_null_count else 0.0
        inferred_type = _infer_type(name, series, non_null)
        normalized_name = name.strip().lower()

        is_sensitive = any(token in normalized_name for token in _SENSITIVE_TOKENS)
        looks_named_id = (
            normalized_name == "id"
            or normalized_name.endswith("_id")
            or any(token in normalized_name for token in _ID_TOKENS)
        )
        is_suspected_id = bool(
            is_sensitive
            or looks_named_id
            or (
                non_null_count >= 20
                and unique_rate >= 0.98
                and inferred_type in {"categorical", "text"}
            )
        )
        is_high_cardinality = bool(
            inferred_type in {"categorical", "text"}
            and unique_count >= HIGH_CARDINALITY_MIN
            and unique_rate >= HIGH_CARDINALITY_RATE
        )
        is_constant = unique_count <= 1
        is_high_missing = missing_rate >= HIGH_MISSING_RATE
        is_datetime = inferred_type == "datetime"

        column_profile: Dict[str, Any] = {
            "dtype": str(series.dtype),
            "inferred_type": inferred_type,
            "non_null_count": non_null_count,
            "missing_count": int(missing_count),
            "missing_rate": round(missing_rate, 6),
            "unique_count": unique_count,
            "unique_rate": round(unique_rate, 6),
            "is_constant": is_constant,
            "is_high_missing": is_high_missing,
            "is_high_cardinality": is_high_cardinality,
            "is_suspected_id": is_suspected_id,
            "is_sensitive": is_sensitive,
            "is_datetime": is_datetime,
        }
        if inferred_type == "numeric" and non_null_count:
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            if not numeric.empty:
                column_profile.update(
                    {
                        "min": _finite_number(numeric.min()),
                        "max": _finite_number(numeric.max()),
                        "mean": _finite_number(numeric.mean()),
                    }
                )
        columns[name] = column_profile

        if unique_count == 2 and not is_sensitive and not is_suspected_id:
            value_counts = non_null.value_counts(dropna=True, sort=False)
            counts = [
                {
                    "value": _json_safe_value(value),
                    "count": int(count),
                }
                for value, count in value_counts.items()
            ]
            binary_candidates.append(
                {
                    "column": name,
                    "values": [item["value"] for item in counts],
                    "counts": counts,
                    "missing_count": int(missing_count),
                }
            )

        if is_high_missing:
            warnings.append(
                _issue(
                    "HIGH_MISSING_RATE",
                    "Column has at least 50% missing values.",
                    [name],
                    {"missing_rate": round(missing_rate, 6)},
                )
            )
        if is_sensitive:
            warnings.append(
                _issue(
                    "SENSITIVE_COLUMN",
                    "Column name suggests personal or sensitive information.",
                    [name],
                )
            )

    if row_count == 0:
        warnings.append(_issue("EMPTY_DATASET", "Dataset contains no data rows."))
    if bool(df.attrs.get("truncated")):
        warnings.append(
            _issue(
                "PROFILE_TRUNCATED",
                "Profile is based on a bounded row sample, not the complete file.",
                details={"max_rows": int(df.attrs.get("max_rows", row_count))},
            )
        )

    return {
        "row_count": row_count,
        "column_count": int(len(df.columns)),
        "duplicate_row_count": int(df.duplicated().sum()) if row_count else 0,
        "truncated": bool(df.attrs.get("truncated", False)),
        "source_sha256": df.attrs.get("source_sha256"),
        "columns": columns,
        "binary_candidates": binary_candidates,
        "warnings": warnings,
    }


def _infer_type(name: str, series: pd.Series, non_null: pd.Series) -> str:
    if non_null.empty:
        return "empty"
    if ptypes.is_bool_dtype(series.dtype):
        return "boolean"
    if ptypes.is_datetime64_any_dtype(series.dtype):
        return "datetime"
    if ptypes.is_numeric_dtype(series.dtype):
        return "numeric"

    normalized_name = name.strip().lower()
    date_named = any(token in normalized_name for token in _DATE_TOKENS)
    if date_named:
        sample = non_null.astype(str).head(1000)
        try:
            parsed = pd.to_datetime(sample, errors="coerce")
            if float(parsed.notna().mean()) >= 0.80:
                return "datetime"
        except (TypeError, ValueError, OverflowError):
            pass
    unique_count = int(non_null.nunique(dropna=True))
    unique_rate = float(unique_count / len(non_null))
    if unique_count <= 50 or unique_rate <= 0.20:
        return "categorical"
    return "text"


def _finite_number(value: Any) -> Any:
    number = float(value)
    if pd.isna(number) or number in (float("inf"), float("-inf")):
        return None
    return round(number, 6)


def _json_safe_value(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    return str(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(
    code: str,
    message: str,
    columns: List[str] = None,
    details: Dict[str, Any] = None,
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "columns": columns or [],
        "details": details or {},
    }
