from __future__ import annotations

import numpy as np
import pandas as pd

from app.agent import build_safe_evidence, generate_reproducible_code, propose_plan, review_generated_code, review_plan
from app.worker import profile_table, segment_analysis, select_features, split_frame, target_summary, train_candidates


def make_frame(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    income = rng.normal(8500, 2200, rows).clip(1800, 30000)
    utilization = rng.beta(2, 5, rows)
    inquiries = rng.poisson(2.5, rows)
    channel = rng.choice(["online", "branch", "partner"], rows, p=[0.6, 0.25, 0.15])
    logit = -1.1 - income / 15000 + utilization * 2.7 + inquiries * 0.25 + (channel == "partner") * 0.4
    probability = 1 / (1 + np.exp(-logit))
    target = (rng.random(rows) < probability).astype(int)
    return pd.DataFrame(
        {
            "customer_id": [f"c-{index}" for index in range(rows)],
            "income": income,
            "utilization": utilization,
            "inquiries_30d": inquiries,
            "channel": channel,
            "after_repayment_overdue": rng.integers(0, 2, rows),
            "bad_flag": target,
        }
    )


def test_profile_target_selection_and_safe_evidence() -> None:
    frame = make_frame()
    profile = profile_table(frame)
    assert profile["rows"] == 600
    assert "bad_flag" in profile["target_candidates"]
    summary = target_summary(frame, "bad_flag")
    assert summary["contract_ok"] is True
    assert summary["positive_count"] + summary["negative_count"] == 600

    plan = propose_plan(profile, summary, "semi_trust", gateway=type("Gateway", (), {"status": lambda _: {"mode": "test"}})())
    review = review_plan(plan, profile, summary)
    assert review["verdict"] == "block"
    assert any(item["code"] == "SUSPECTED_POST_OUTCOME_FEATURE" for item in review["findings"])
    evidence = build_safe_evidence(profile, summary)
    assert evidence["suppression"]["raw_rows_included"] is False
    assert all("name" not in item for item in evidence["fields"])


def test_feature_selection_reports_funnel_and_blocks_leakage() -> None:
    frame = make_frame()
    selection = select_features(frame, "bad_flag", max_features=3)
    assert len(selection["selected"]) == 3
    assert selection["funnel"]["blocked"] == 1
    by_column = {item["column"]: item for item in selection["decisions"]}
    assert by_column["customer_id"]["status"] == "excluded"
    assert by_column["after_repayment_overdue"]["status"] == "blocked"


def test_train_candidates_and_segment_analysis(tmp_path) -> None:
    frame = make_frame()
    split = split_frame(frame, "bad_flag")
    features = ["income", "utilization", "inquiries_30d", "channel"]
    result = train_candidates(frame, "bad_flag", features, split, tmp_path / "models")
    successful = [item for item in result["candidates"] if item["status"] == "succeeded"]
    names = {item["name"] for item in successful}
    assert {"logistic_regression", "random_forest", "hist_gradient_boosting"}.issubset(names)
    assert result["champion"] is not None
    assert result["champion"]["validation"]["roc_auc"] is not None
    assert result["champion"]["validation_lift"]
    assert result["scorecard"]["route"] == "woe_logistic_proxy"

    analysis = segment_analysis(
        frame,
        {
            "dimensions": [{"column": "channel"}],
            "target": {"column": "bad_flag"},
            "min_group_size": 20,
        },
    )
    assert analysis["dimensions"] == ["channel"]
    assert sum(item["row_count"] for item in analysis["rows"]) == len(frame)


def test_generated_code_review_blocks_network_and_allows_reference_code() -> None:
    assert review_generated_code("import pandas as pd\nprint('ok')")["verdict"] == "pass"
    result = review_generated_code("import requests\nrequests.get('https://example.com')")
    assert result["verdict"] == "block"
    assert any(item["code"] == "DANGEROUS_NETWORK" for item in result["findings"])

    frame = make_frame()
    profile = profile_table(frame)
    code = generate_reproducible_code({"target": "bad_flag"}, ["income", "channel"], profile)
    compile(code, "generated_model.py", "exec")
    assert "OneHotEncoder" in code


def test_plan_review_blocks_too_small_target_class() -> None:
    frame = make_frame(40)
    frame["bad_flag"] = 0
    frame.loc[0, "bad_flag"] = 1
    profile = profile_table(frame)
    summary = target_summary(frame, "bad_flag")
    plan = propose_plan(profile, summary, "auto", gateway=type("Gateway", (), {"status": lambda _: {}})())
    review = review_plan(plan, profile, summary)
    assert review["verdict"] == "block"
    assert any(item["code"] == "TARGET_CLASS_TOO_SMALL" for item in review["findings"])
