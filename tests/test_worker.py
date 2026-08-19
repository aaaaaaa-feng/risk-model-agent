from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import agent as agent_module
from app.agent import ProviderGateway, build_safe_evidence, generate_reproducible_code, propose_plan, repair_generated_code, review_generated_code, review_plan
from app.worker import build_cleaning_plan, profile_table, quality_analysis, read_table, segment_analysis, select_features, split_frame, target_summary, train_candidates


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


def test_quality_and_cleaning_plan_are_local_and_non_destructive() -> None:
    frame = make_frame()
    frame.loc[0, "channel"] = " online "
    frame.loc[1, "channel"] = "   "
    quality = quality_analysis(frame, target="bad_flag")
    plan = build_cleaning_plan(frame, profile_table(frame), quality)
    assert quality["rows"] == len(frame)
    assert quality["target"]["contract_ok"] is True
    assert plan["rows_before"] == plan["rows_after"] == len(frame)
    assert plan["columns_before"] == plan["columns_after"]
    assert plan["actions"][0]["code"] == "TRIM_TEXT_AND_BLANKS"
    assert all("raw" not in json.dumps(item, ensure_ascii=False).lower() for item in quality["categorical"])


def test_csv_reader_accepts_gb18030(tmp_path: Path) -> None:
    path = tmp_path / "gbk.csv"
    path.write_bytes("bad_flag,渠道\n0,网点\n1,线上\n".encode("gb18030"))
    frame = read_table(path)
    assert list(frame.columns) == ["bad_flag", "渠道"]
    assert frame["渠道"].tolist() == ["网点", "线上"]


def test_feature_selection_reports_funnel_and_blocks_leakage() -> None:
    frame = make_frame()
    selection = select_features(frame, "bad_flag", max_features=3)
    assert len(selection["selected"]) == 3
    assert selection["funnel"]["blocked"] == 1
    by_column = {item["column"]: item for item in selection["decisions"]}
    assert by_column["customer_id"]["status"] == "excluded"
    assert by_column["after_repayment_overdue"]["status"] == "blocked"


def test_feature_selection_can_be_scoped_to_train_rows() -> None:
    frame = make_frame()
    fit_positions = list(range(420))
    selection = select_features(frame, "bad_flag", max_features=3, fit_positions=fit_positions)
    assert selection["funnel"]["fit_scope"] == "train"
    assert selection["funnel"]["fit_rows"] == len(fit_positions)
    assert all(item["iv"] is None or item["iv"] >= 0 for item in selection["decisions"])


def test_model_selection_is_explicit(tmp_path) -> None:
    frame = make_frame()
    split = split_frame(frame, "bad_flag")
    result = train_candidates(frame, "bad_flag", ["income", "utilization", "inquiries_30d", "channel"], split, tmp_path / "models", model_names=["logistic_regression"])
    assert result["models_requested"] == ["logistic_regression"]
    assert [item["name"] for item in result["candidates"]] == ["logistic_regression"]


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


def test_segment_analysis_caps_combination_explosion() -> None:
    frame = pd.DataFrame({f"d{index}": [f"v{row % 40}" for row in range(600)] for index in range(4)})
    frame["bad_flag"] = [row % 2 for row in range(600)]
    spec = {"dimensions": [{"column": f"d{index}"} for index in range(4)], "target": {"column": "bad_flag"}, "max_groups": 100}
    try:
        segment_analysis(frame, spec)
    except ValueError as exc:
        assert "GROUP_LIMIT_EXCEEDED" in str(exc)
    else:
        raise AssertionError("expected high-dimensional grouping guard")


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
    repaired, metadata = repair_generated_code("import requests", {"target": "bad_flag"}, ["income"], [{"code": "DANGEROUS_NETWORK"}], profile)
    assert "requests" not in repaired
    assert metadata["generated_code_executed"] is False


def test_provider_gateway_requires_opt_in_and_blocks_sensitive_payload(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "provider_key", lambda: "test-key")
    config = {
        "llm_enabled": False,
        "base_url": "https://provider.example/v1",
        "model": "main-model",
    }
    gateway = ProviderGateway(config=config)
    disabled = gateway.complete("system", {"schema_version": "risk-safe-evidence/v1"})
    assert disabled.error_code == "PROVIDER_DISABLED"

    config["llm_enabled"] = True
    blocked = ProviderGateway(config=config).complete("system", {"raw_rows": [{"x": 1}]})
    assert blocked.error_code == "DLP_BLOCK"


def test_provider_gateway_sends_only_safe_payload(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "provider_key", lambda: "test-key")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"status":"ok"}'}}], "usage": {"total_tokens": 3}}

    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return Response()

    gateway = ProviderGateway(
        config={"llm_enabled": True, "base_url": "https://provider.example/v1", "model": "main-model"},
        client_factory=Client,
    )
    result = gateway.complete("Use aliases only", {"fields": [{"alias": "f_0001", "missing_rate": 0.1}]})
    assert result.ok is True
    assert captured["url"].endswith("/chat/completions")
    assert "test-key" in captured["headers"]["Authorization"]
    assert "f_0001" in captured["json"]["messages"][1]["content"]
    assert "raw_rows" not in captured["json"]["messages"][1]["content"]


def test_provider_gateway_budget_guard_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "provider_key", lambda: "test-key")
    gateway = ProviderGateway(
        config={"llm_enabled": True, "base_url": "https://provider.example/v1", "model": "main-model"},
        budget_guard=lambda requested: "budget reached",
    )
    result = gateway.complete("system", {"schema_version": "risk-safe-evidence/v1"})
    assert result.error_code == "PROVIDER_BUDGET_EXCEEDED"


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
