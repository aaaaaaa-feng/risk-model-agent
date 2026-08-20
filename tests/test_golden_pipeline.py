from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import openpyxl

from app.core.security import sha256_file


def test_full_agent_reviewer_worker_pipeline(golden):
    context = golden["context"]
    run = context.catalog.require("runs", golden["run"]["id"])
    state = run["state"]
    assert run["status"] == "succeeded"
    assert state["split"]["customer_key"] == "customer_id"
    frame = context.catalog.dataset_frame(state["working_dataset_version_id"])
    partitions = state["split"]["indices"]
    customer_sets = {
        name: set(frame.iloc[indices]["customer_id"])
        for name, indices in partitions.items()
    }
    assert customer_sets["train"].isdisjoint(customer_sets["test"])
    assert customer_sets["train"].isdisjoint(customer_sets["oot"])
    assert customer_sets["test"].isdisjoint(customer_sets["oot"])
    assert state["model_result"]["oot_used_for_selection"] is False
    statuses = {item["candidate"]: item["status"] for item in state["model_result"]["candidates"]}
    assert statuses == {
        "dummy": "trained",
        "scorecard": "trained",
        "regularized_logistic": "trained",
        "xgboost": "trained",
    }
    excluded = {item["column"]: item["reason"] for item in state["screening"]["excluded"]}
    assert excluded["FPD7"] == "OTHER_TARGET"
    assert excluded["MOB30"] == "OTHER_TARGET"
    reviews = context.database.list("review_records", {"run_id": run["id"]}, limit=200)
    assert {"code", "execution", "report"}.issubset({item["scope"] for item in reviews})
    assert all(item["status"] == "pass" for item in reviews)
    assert context.engine.persistence_mode == "sqlite"


def test_report_excel_html_json_share_one_structured_source(golden):
    context = golden["context"]
    run = context.catalog.require("runs", golden["run"]["id"])
    artifacts = context.database.list("artifacts", {"run_id": run["id"]}, limit=100)
    by_kind = {item["kind"]: item for item in artifacts}
    assert {"report_json", "report_excel", "report_html", "model_package", "reproducible_notebook"}.issubset(by_kind)
    for artifact in artifacts:
        assert Path(artifact["path"]).exists()
        assert sha256_file(Path(artifact["path"])) == artifact["checksum"]
    report = json.loads(Path(by_kind["report_json"]["path"]).read_text(encoding="utf-8"))
    html = Path(by_kind["report_html"]["path"]).read_text(encoding="utf-8")
    assert report["schema_version"] == "risk-model-report/v1"
    assert report["executive_summary"]["champion"] == report["champion"]["candidate"]
    expected_verdict = (
        "pass" if report["executive_summary"]["absolute_ordering"] else "conditional"
    )
    assert report["executive_summary"]["quality_verdict"] == expected_verdict
    if expected_verdict == "conditional":
        assert report["executive_summary"]["quality_notes"]
    assert 'id="risk-model-report-data"' in html
    assert json.dumps(report["executive_summary"]["champion"], ensure_ascii=False) in html
    workbook = openpyxl.load_workbook(by_kind["report_excel"]["path"], read_only=True, data_only=True)
    assert workbook.sheetnames[:3] == ["总体情况", "入模变量", "模型分箱"]
    assert workbook["总体情况"]["C3"].value == report["executive_summary"]["champion"]
    feature_rows = list(workbook["入模变量"].iter_rows(values_only=True))
    flattened = {str(value) for row in feature_rows for value in row if value is not None}
    assert {
        item["column"] for item in report["feature_selection"]["selected"]
    }.issubset(flattened)


def test_persisted_model_reload_scores_identically_and_names_columns(golden):
    context = golden["context"]
    run = context.catalog.require("runs", golden["run"]["id"])
    model = context.database.list("model_versions", {"run_id": run["id"]}, limit=10)[0]
    frame = context.catalog.dataset_frame(golden["demo"]["dataset_version"]["id"])
    source = Path(golden["demo"]["dataset_version"]["stored_path"])
    asset = context.catalog.register_asset(
        golden["demo"]["project"]["id"], source, "score_input.csv", "score_input"
    )
    first_job, first_artifact = context.artifacts.score_file(model["id"], asset["id"])
    second_job, second_artifact = context.artifacts.score_file(model["id"], asset["id"])
    import pandas as pd

    first = pd.read_csv(first_artifact["path"])
    second = pd.read_csv(second_artifact["path"])
    score_column = first_job["metadata"]["score_column"]
    assert score_column.startswith(model["name"].replace("-", "_"))
    assert np.array_equal(first[score_column].to_numpy(), second[score_column].to_numpy())
    assert np.allclose(
        first[f"{score_column}_bad_probability"],
        second[f"{score_column}_bad_probability"],
    )
    assert first[score_column].between(300, 900).all()
    assert second_job["rows"] == len(frame)


def test_model_package_contract_and_hashes(golden):
    context = golden["context"]
    run = context.catalog.require("runs", golden["run"]["id"])
    state = run["state"]
    manifest = state["package_manifest"]
    assert manifest["raw_data_included"] is False
    assert any(item["format"] == "skops" for item in manifest["formats"])
    champion = state["model_result"]["champion"]
    expected_native = {
        "xgboost": "xgboost-json",
        "lightgbm": "lightgbm-text",
        "catboost": "catboost-cbm",
    }.get(champion)
    if expected_native:
        assert any(item["format"] == expected_native for item in manifest["formats"])
    package = next(
        item for item in context.database.list("artifacts", {"run_id": run["id"]}, limit=100)
        if item["kind"] == "model_package"
    )
    assert manifest["package_sha256"] == sha256_file(Path(package["path"]))
