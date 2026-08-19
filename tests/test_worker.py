from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import agent as agent_module
from app.agent import ProviderGateway, _safe_plan_payload, answer_chat, build_safe_evidence, generate_reproducible_code, propose_plan, repair_generated_code, review_generated_code, review_plan
from app.tools import registry_manifest, require_tool
from app.worker import build_cleaning_plan, estimate_table_resources, evaluate_baseline, parse_data_dictionary, profile_table, quality_analysis, read_table, segment_analysis, select_features, split_frame, target_summary, train_candidates


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


def test_resource_estimate_is_explicit_and_blocks_before_materialization(tmp_path: Path, monkeypatch) -> None:
    import app.worker as worker_module

    path = tmp_path / "bounded.csv"
    path.write_text("bad_flag,income\n0,100\n1,50\n", encoding="utf-8")
    estimate = estimate_table_resources(path)
    assert estimate["rows"] == 2
    assert estimate["columns"] == 2
    assert estimate["risk"] == "ok"
    assert estimate["exact"] is False

    monkeypatch.setattr(worker_module, "MAX_ROWS", 1)
    try:
        read_table(path)
    except ValueError as exc:
        assert "ROW_LIMIT_EXCEEDED" in str(exc)
    else:
        raise AssertionError("resource guard must reject an oversized CSV before loading")


def test_data_dictionary_maps_local_semantics_without_changing_source_columns() -> None:
    dictionary = parse_data_dictionary(pd.DataFrame({"字段名": ["income", "bad_flag"], "中文名": ["收入", "是否违约"], "口径": ["申请月收入", "观察窗内坏样本"]}))
    frame = pd.DataFrame({"income": [100, 200], "bad_flag": [0, 1]})
    profile = profile_table(frame, dictionary)
    assert dictionary["field_count"] == 2
    assert profile["dictionary"]["matched_count"] == 2
    assert profile["columns_detail"][0]["dictionary"]["display_name"] == "收入"
    assert list(frame.columns) == ["income", "bad_flag"]


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
    assert result["scorecard"]["route"] == "woe_logistic"
    assert result["scorecard"]["points"]
    assert result["scorecard"]["base_score"] == 600.0
    assert result["scorecard"]["score_mapping_check"]["passed"] is True
    assert result["stability"]["schema_version"] == "risk-stability/v1"
    assert result["champion"]["feature_importance"]
    assert result["champion"]["validation"]["calibration"]
    assert result["champion"]["oof"]["status"] == "succeeded"

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
    assert any(item["code"] == "DANGEROUS_IMPORT" for item in result["findings"])
    syntax = review_generated_code("def broken(:\n    pass")
    assert syntax["verdict"] == "block"
    assert any(item["code"] == "CODE_SYNTAX_INVALID" for item in syntax["findings"])
    assert any(item["code"] == "DANGEROUS_NETWORK" for item in result["findings"])

    frame = make_frame()
    profile = profile_table(frame)
    code = generate_reproducible_code({"target": "bad_flag"}, ["income", "channel"], profile)
    compile(code, "generated_model.py", "exec")
    assert "OneHotEncoder" in code
    scorecard_code = generate_reproducible_code({"target": "bad_flag", "models": ["woe_logistic_scorecard"]}, ["income", "channel"], profile)
    compile(scorecard_code, "generated_scorecard.py", "exec")
    assert "BASE_SCORE = 600.0" in scorecard_code and "_fit_specs" in scorecard_code
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


def test_safe_plan_aliases_excluded_columns_before_provider_boundary() -> None:
    profile = {"columns_detail": [{"name": "post_loan_overdue_days", "type": "numeric"}]}
    payload = _safe_plan_payload(
        {"target": "post_loan_overdue_days", "screening": {"excluded_columns": ["post_loan_overdue_days"]}},
        profile,
        {"target": "post_loan_overdue_days", "contract_ok": True},
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "post_loan_overdue_days" not in serialized
    assert "f_0001" in serialized


def test_provider_gateway_budget_guard_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "provider_key", lambda: "test-key")
    gateway = ProviderGateway(
        config={"llm_enabled": True, "base_url": "https://provider.example/v1", "model": "main-model"},
        budget_guard=lambda requested: "budget reached",
    )
    result = gateway.complete("system", {"schema_version": "risk-safe-evidence/v1"})
    assert result.error_code == "PROVIDER_BUDGET_EXCEEDED"


def test_chat_dlp_blocks_suspected_pasted_identifier_before_provider_call(monkeypatch) -> None:
    monkeypatch.setattr(agent_module, "provider_key", lambda: "test-key")
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("疑似敏感聊天不应触发外部请求")

    gateway = ProviderGateway(
        config={"llm_enabled": True, "base_url": "https://provider.example/v1", "model": "main-model"},
        client_factory=Client,
    )
    answer = answer_chat("请帮我看手机号 13800138000 的样本", {}, gateway)
    assert answer["provider_call"]["error_code"] == "CHAT_DLP_BLOCK"
    assert calls == []


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


def test_tool_registry_is_allowlisted_and_node_scoped() -> None:
    assert any(item["name"] == "train_candidate" for item in registry_manifest())
    assert require_tool("train_candidate", "training").execution_class == "sandboxed_process"
    try:
        require_tool("unknown_tool", "training")
    except ValueError as exc:
        assert "TOOL_NOT_REGISTERED" in str(exc)
    else:
        raise AssertionError("unregistered tools must be rejected")


def test_baseline_uses_validation_orientation_and_freezes_oot_threshold() -> None:
    frame = pd.DataFrame(
        {
            "bad_flag": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
            "existing_score": [900, 100, 850, 120, 800, 150, 780, 180, 760, 200, 740, 220],
        }
    )
    split = {"positions": list(range(len(frame))), "train": [0, 1, 2, 3, 4, 5], "valid": [6, 7, 8, 9], "oot": [10, 11]}
    result = evaluate_baseline(frame, "bad_flag", "existing_score", split)
    assert result["orientation"] == "higher_is_good"
    assert result["validation"]["roc_auc"] == 1.0
    assert result["oot"]["threshold"] == result["validation"]["threshold"]
    assert result["oot_used_for_selection"] is False
    assert result["validation_fixed_rate"]["approval_rate"] == 0.75
