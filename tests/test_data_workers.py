from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from app.workers.binning import apply_manual_binning, fit_binning
from app.workers.demo import generate_demo_tables, install_demo_project
from app.workers.io import plan_resources, recommended_batches
from app.workers.joining import JoinStep, execute_join, validate_join
from app.workers.metrics import binary_metrics, lift_table, psi
from app.workers.profiling import profile_frame, target_summary
from app.workers.screening import restore_features, screen_features
from app.workers.splitting import freeze_target_samples, split_dataset


def test_fixed_seed_demo_has_multitable_multiple_y_and_dictionary():
    first = generate_demo_tables(600)
    second = generate_demo_tables(600)
    assert set(first) == {"base", "demographics", "bureau", "device", "dictionary"}
    assert first["base"].equals(second["base"])
    assert {"FPD0", "FPD7", "MOB30"}.issubset(first["base"])
    assert set(first["base"]["FPD7"].dropna().unique()) == {-1.0, 0.0, 1.0}
    assert first["base"]["FPD7"].isna().any()


def test_demo_install_exercises_xlsx_multisheet_join_and_lineage(context):
    demo = install_demo_project(context.catalog, rows=600)
    base = demo["assets"][0]
    assert base["format"] == "xlsx"
    assert base["sheet"] == "放款订单"
    assert "字段说明" in (base["metadata"]["sheets"])
    dataset = demo["dataset_version"]
    assert dataset["rows"] == 600
    assert len(dataset["lineage"]["steps"]) == 3
    assert set(dataset["lineage"]["checked_targets"]) == {"FPD0", "FPD7", "MOB30"}
    assert [task["target_column"] for task in demo["target_tasks"]] == ["FPD0", "FPD7", "MOB30"]


def test_join_blocks_duplicate_right_keys_and_preserves_target_distribution():
    left = pd.DataFrame({"id": [1, 2, 3], "Y": [0, 1, 0]})
    duplicate = pd.DataFrame({"id": [1, 1, 2], "x": [1, 2, 3]})
    validation = validate_join(left, duplicate, ["id"], ["id"], ["Y"], "id")
    assert any(item["code"] == "RIGHT_KEY_DUPLICATES" for item in validation["issues"])
    with pytest.raises(ValueError, match="JOIN_VALIDATION_BLOCKED"):
        execute_join(left, duplicate, JoinStep("asset", ["id"], ["id"]), ["Y"], "id")


def test_target_freeze_and_customer_isolated_oot_split():
    tables = generate_demo_tables(800)
    frame = tables["base"]
    frozen, evidence = freeze_target_samples(frame, "FPD7")
    assert evidence["excluded_rows"] > 0
    assert set(frozen["FPD7"].unique()) == {0, 1}
    split = split_dataset(
        frozen,
        "FPD7",
        method="time_holdout",
        time_column="application_date",
        customer_key="customer_id",
    )
    groups = {
        name: set(frozen.iloc[indices]["customer_id"]) for name, indices in split["indices"].items()
    }
    assert not (groups["train"] & groups["test"])
    assert not (groups["train"] & groups["oot"])
    assert not (groups["test"] & groups["oot"])
    assert split["oot_locked"] is True
    assert split["fit_scope"] == "train_only"
    development = split["indices"]["train"] + split["indices"]["test"]
    times = pd.to_datetime(frozen["application_date"])
    assert times.iloc[development].max() < times.iloc[split["indices"]["oot"]].min()
    assert len(development) + len(split["indices"]["oot"]) + len(split["excluded_indices"]) == len(
        frozen
    )
    assert split["strict_time_boundary"] is True


def test_time_oot_excludes_cross_boundary_customers_instead_of_backdating_oot():
    frame = pd.DataFrame(
        {
            "customer_id": [f"C{index:03d}" for index in range(38)] + ["CROSS", "CROSS"],
            "application_date": pd.date_range("2025-01-01", periods=38).tolist()
            + [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-03-01")],
            "Y": [0, 1] * 20,
        }
    )
    split = split_dataset(
        frame,
        "Y",
        method="time_holdout",
        time_column="application_date",
        customer_key="customer_id",
        oot_size=0.2,
    )
    excluded_customers = set(frame.iloc[split["excluded_indices"]]["customer_id"])
    assert "CROSS" in excluded_customers
    development = split["indices"]["train"] + split["indices"]["test"]
    parsed = pd.to_datetime(frame["application_date"])
    assert parsed.iloc[development].max() < parsed.iloc[split["indices"]["oot"]].min()


def test_screening_blocks_other_y_pii_identifiers_and_leakage():
    tables = generate_demo_tables(800)
    frame = (
        tables["base"]
        .merge(tables["demographics"], on="customer_id", how="left", validate="many_to_one")
        .merge(tables["bureau"], on="order_id", how="left", validate="one_to_one")
    )
    frozen, _ = freeze_target_samples(frame, "FPD0")
    result = screen_features(
        frozen,
        "FPD0",
        protected_targets=["FPD0", "FPD7", "MOB30"],
    )
    reasons = {item["column"]: item["reason"] for item in result["excluded"]}
    assert reasons["FPD7"] == "OTHER_TARGET"
    assert reasons["MOB30"] == "OTHER_TARGET"
    assert reasons["order_id"] == "IDENTIFIER"
    assert reasons["customer_id"] == "IDENTIFIER"
    assert reasons["post_collection_status"] == "LEAKAGE"
    with pytest.raises(ValueError, match="FEATURE_NOT_RECOVERABLE"):
        restore_features(result, [{"column": "FPD7", "reason": "业务确认可以恢复该变量"}])


def test_recoverable_feature_requires_reason():
    frame = pd.DataFrame(
        {"Y": [0, 1] * 100, "weak": np.arange(200) % 2, "missing": [None] * 70 + list(range(130))}
    )
    result = screen_features(frame, "Y", iv_threshold=99, missing_threshold=0.3)
    with pytest.raises(ValueError, match="RESTORE_REASON_REQUIRED"):
        restore_features(result, [{"column": "weak", "reason": "短"}])
    restored = restore_features(
        result, [{"column": "weak", "reason": "经业务和稳定性复核后确认保留"}]
    )
    assert "weak" in restored["included"]


def test_manual_binning_versions_and_rejects_non_monotonic():
    frame = pd.DataFrame({"x": np.arange(120), "Y": [0] * 40 + [1] * 40 + [0] * 40})
    fitted = fit_binning(frame, "Y", ["x"])
    original = fitted["version"]
    updated = apply_manual_binning(
        copy.deepcopy(fitted), frame, "Y", "x", {"kind": "numeric", "edges": [39.5]}
    )
    assert updated["version"] != original
    assert updated["specs"]["x"]["source"] == "manual"
    assert updated["invalidates"] == ["training", "review", "report"]
    with pytest.raises(ValueError, match="MANUAL_BIN_NOT_MONOTONIC"):
        apply_manual_binning(
            copy.deepcopy(fitted), frame, "Y", "x", {"kind": "numeric", "edges": [39.5, 79.5]}
        )
    exception = apply_manual_binning(
        copy.deepcopy(fitted),
        frame,
        "Y",
        "x",
        {
            "kind": "numeric",
            "edges": [39.5, 79.5],
            "business_exception": "业务确认该变量保留原始分箱",
        },
    )
    assert exception["specs"]["x"]["exception_status"] == "accepted_with_business_exception"
    assert exception["specs"]["x"]["monotonic"] is False


def test_metrics_have_expected_semantics():
    y = np.array([0, 0, 0, 1, 1, 1])
    p = np.array([0.02, 0.1, 0.2, 0.7, 0.8, 0.95])
    metrics = binary_metrics(y, p)
    assert metrics["roc_auc"] == pytest.approx(1)
    assert metrics["ks"] == pytest.approx(1)
    assert lift_table(y, p)[0]["bad_rate"] == 1
    assert psi(p, p) == pytest.approx(0)


def test_30000_by_1000_resource_plan_batches_without_silent_sampling():
    values = np.zeros((30_000, 1_000), dtype=np.int8)
    frame = pd.DataFrame(values, columns=[f"f_{index}" for index in range(1_000)])
    plan = plan_resources(len(frame), len(frame.columns), memory_budget_mb=512)
    batches = recommended_batches(frame, memory_budget_mb=512)
    assert sum(len(batch) for batch in batches) == 1_000
    assert max(map(len, batches)) <= 128
    assert plan.row_chunk_size <= 100_000
    assert plan.max_parallel_models in {1, 2}
    assert len(frame) == 30_000


def test_profile_never_includes_raw_rows():
    profile = profile_frame(pd.DataFrame({"Y": [0, 1] * 60, "x": range(120)}))
    assert "raw_rows" not in profile
    assert target_summary(pd.DataFrame({"Y": [0, 1] * 60}), "Y")["valid_count"] == 120
