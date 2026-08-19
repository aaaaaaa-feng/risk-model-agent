import pandas as pd
import pytest

from app.domain import DomainError
from app.services.profiling import load_csv, profile_dataframe


def test_load_csv_is_bounded_and_profile_contains_no_row_samples(tmp_path):
    path = tmp_path / "dataset.csv"
    path.write_text("age,target\n20,0\n30,1\n40,0\n", encoding="utf-8")

    df = load_csv(path, max_rows=2)
    profile = profile_dataframe(df)

    assert len(df) == 2
    assert profile["truncated"] is True
    assert profile["row_count"] == 2
    assert profile["source_sha256"]
    assert "sample_values" not in profile["columns"]["age"]
    assert any(item["code"] == "PROFILE_TRUNCATED" for item in profile["warnings"])


def test_profile_detects_sensitive_id_missing_constant_date_and_cardinality():
    size = 100
    df = pd.DataFrame(
        {
            "customer_id": [f"C-{index}" for index in range(size)],
            "phone": [f"1380000{index:04d}" for index in range(size)],
            "gender": ["female", "male"] * 50,
            "constant": [1] * size,
            "mostly_missing": [None] * 60 + list(range(40)),
            "application_date": ["2025-01-01"] * size,
            "category": [f"unique-{index}" for index in range(size)],
        }
    )

    profile = profile_dataframe(df)

    assert profile["columns"]["customer_id"]["is_suspected_id"] is True
    assert profile["columns"]["phone"]["is_sensitive"] is True
    assert profile["columns"]["gender"]["is_sensitive"] is True
    assert profile["columns"]["constant"]["is_constant"] is True
    assert profile["columns"]["mostly_missing"]["is_high_missing"] is True
    assert profile["columns"]["application_date"]["is_datetime"] is True
    assert profile["columns"]["category"]["is_high_cardinality"] is True


def test_profile_exposes_aggregate_binary_candidates_without_row_samples():
    df = pd.DataFrame(
        {
            "decision": ["approve", "decline", "approve"],
            "phone": ["13800000001", "13800000002", "13800000001"],
        }
    )

    profile = profile_dataframe(df)

    candidate = next(item for item in profile["binary_candidates"] if item["column"] == "decision")
    assert set(candidate["values"]) == {"approve", "decline"}
    assert sum(item["count"] for item in candidate["counts"]) == 3
    assert all(item["column"] != "phone" for item in profile["binary_candidates"])


def test_load_csv_rejects_invalid_inputs(tmp_path):
    non_csv = tmp_path / "dataset.xlsx"
    non_csv.write_bytes(b"not an xlsx")
    with pytest.raises(DomainError) as unsupported:
        load_csv(non_csv)
    assert unsupported.value.status_code == 415

    path = tmp_path / "dataset.csv"
    path.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(DomainError) as invalid_limit:
        load_csv(path, max_rows=0)
    assert invalid_limit.value.code == "INVALID_MAX_ROWS"
