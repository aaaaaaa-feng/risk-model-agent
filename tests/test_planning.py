import pandas as pd
import pytest

from app.domain import DomainError
from app.services.planning import build_plan, hash_plan, validate_approval
from app.services.profiling import profile_dataframe


def _clean_dataframe():
    rows = 80
    return pd.DataFrame(
        {
            "age": [20 + index % 40 for index in range(rows)],
            "income": [3000 + index * 25 for index in range(rows)],
            "segment": ["a", "b", "c", "d"] * 20,
            "target": [0, 1] * 40,
        }
    )


def _request():
    return {
        "target": {
            "column": "target",
            "positive_label": 1,
            "negative_label": 0,
        },
        "split": {"method": "stratified_random", "test_size": 0.2, "random_state": 7},
    }


def test_build_plan_exact_shape_hash_and_approval():
    df = _clean_dataframe()
    plan = build_plan(df, profile_dataframe(df), _request(), version=1)

    assert set(plan) == {
        "version",
        "target",
        "features",
        "split",
        "preprocessing",
        "candidates",
        "selection",
        "warnings",
        "blocking_issues",
        "required_confirmations",
        "limitations",
        "plan_hash",
    }
    assert plan["candidates"] == ["dummy", "logistic_regression", "random_forest"]
    assert plan["blocking_issues"] == []
    assert plan["plan_hash"] == hash_plan(plan)
    confirmations = {name: True for name in plan["required_confirmations"]}
    assert validate_approval(plan, plan["plan_hash"], confirmations) is True


def test_target_missing_non_binary_and_small_minority_are_blocking():
    target = [0] * 40 + [1] * 10 + [-1, None]
    df = pd.DataFrame({"feature": range(len(target)), "target": target})
    plan = build_plan(df, profile_dataframe(df), _request(), version=1)
    codes = {issue["code"] for issue in plan["blocking_issues"]}

    assert "TARGET_HAS_MISSING_VALUES" in codes
    assert "TARGET_NOT_BINARY" in codes
    assert "TARGET_MINORITY_TOO_SMALL" in codes
    with pytest.raises(DomainError) as blocked:
        validate_approval(
            plan,
            plan["plan_hash"],
            {name: True for name in plan["required_confirmations"]},
        )
    assert blocked.value.code == "PLAN_BLOCKED"


def test_negative_label_is_inferred_for_string_binary_target():
    df = pd.DataFrame(
        {
            "feature": list(range(80)),
            "target": ["good", "bad"] * 40,
        }
    )
    request = {
        "target": {"column": "target", "positive_label": "bad"},
        "split": {"method": "stratified_random"},
    }

    plan = build_plan(df, profile_dataframe(df), request, version=1)

    assert plan["target"]["positive_label"] == "bad"
    assert plan["target"]["negative_label"] == "good"
    assert plan["target"]["positive_count"] == 40
    assert plan["target"]["negative_count"] == 40
    assert not any(issue["code"] == "TARGET_NOT_BINARY" for issue in plan["blocking_issues"])


def test_feature_policy_drops_risky_columns_and_blocks_leakage():
    rows = 80
    target = [0, 1] * 40
    df = pd.DataFrame(
        {
            "age": [20 + index % 30 for index in range(rows)],
            "customer_id": [f"C-{index}" for index in range(rows)],
            "constant": [1] * rows,
            "mostly_missing": [None] * 50 + list(range(30)),
            "prior_delinquencies": [index % 4 for index in range(rows)],
            "target_copy": target,
            "days_past_due_30": [0, 10] * 40,
            "target": target,
        }
    )
    plan = build_plan(df, profile_dataframe(df), _request(), version=1)
    dropped = set(plan["features"]["dropped_columns"])
    blockers = {issue["code"] for issue in plan["blocking_issues"]}

    assert {
        "customer_id",
        "constant",
        "mostly_missing",
        "target_copy",
        "days_past_due_30",
    } <= dropped
    assert "TARGET_COPY_DETECTED" in blockers
    assert "SUSPECTED_POST_OUTCOME_FEATURE" in blockers
    assert "prior_delinquencies" in plan["features"]["included_columns"]
    assert not any(
        issue["code"] == "SUSPECTED_POST_OUTCOME_FEATURE"
        and issue["columns"] == ["prior_delinquencies"]
        for issue in plan["blocking_issues"]
    )
    assert any(
        issue["code"] == "HISTORICAL_RISK_FEATURE_REVIEW"
        and issue["columns"] == ["prior_delinquencies"]
        for issue in plan["warnings"]
    )
    assert any("heuristic" in limitation.lower() for limitation in plan["limitations"])


def test_hash_change_or_missing_confirmation_cannot_be_approved():
    df = _clean_dataframe()
    plan = build_plan(df, profile_dataframe(df), _request(), version=1)

    with pytest.raises(DomainError) as wrong_hash:
        validate_approval(plan, "0" * 64, plan["required_confirmations"])
    assert wrong_hash.value.code == "PLAN_HASH_MISMATCH"

    with pytest.raises(DomainError) as incomplete:
        validate_approval(plan, plan["plan_hash"], ["target_definition"])
    assert incomplete.value.code == "CONFIRMATIONS_INCOMPLETE"

    plan["split"]["random_state"] = 99
    with pytest.raises(DomainError) as stale:
        validate_approval(plan, plan["plan_hash"], plan["required_confirmations"])
    assert stale.value.code == "PLAN_HASH_MISMATCH"


def test_time_split_requires_existing_time_column():
    df = _clean_dataframe()
    request = _request()
    request["split"] = {
        "method": "time_holdout",
        "time_column": "application_time",
        "test_size": 0.2,
        "random_state": 42,
    }
    plan = build_plan(df, profile_dataframe(df), request, version=1)
    assert any(issue["code"] == "TIME_COLUMN_NOT_FOUND" for issue in plan["blocking_issues"])
