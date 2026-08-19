from __future__ import annotations

import time
import hashlib
import json
import zipfile
from io import BytesIO

import pandas as pd
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, store
from app.worker import profile_table, target_summary


client = TestClient(app)


def wait_for_status(run_id: str, expected: set[str], timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    latest = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        latest = response.json()["run"]
        if latest["status"] in expected:
            return latest
        time.sleep(0.5)
    raise AssertionError(f"run did not reach {expected}: {latest}")


def create_demo_project(name: str) -> tuple[dict, dict]:
    project = client.post("/api/projects", json={"name": name}).json()["project"]
    dataset_response = client.post(f"/api/projects/{project['id']}/demo")
    dataset_response.raise_for_status()
    return project, dataset_response.json()["dataset"]


def test_project_archive_restore_and_explicit_delete() -> None:
    project, dataset = create_demo_project("项目归档生命周期")
    archived = client.post(f"/api/projects/{project['id']}/archive")
    archived.raise_for_status()
    assert archived.json()["project"]["status"] == "archived"
    blocked_run = client.post(
        f"/api/projects/{project['id']}/runs",
        json={"dataset_id": dataset["id"], "mode": "auto"},
    )
    assert blocked_run.status_code == 409
    restored = client.post(f"/api/projects/{project['id']}/restore")
    restored.raise_for_status()
    assert restored.json()["project"]["status"] == "data_imported"
    archived_again = client.post(f"/api/projects/{project['id']}/archive")
    archived_again.raise_for_status()
    missing_confirmation = client.request("DELETE", f"/api/projects/{project['id']}", json={"confirm": False})
    assert missing_confirmation.status_code == 400
    deleted = client.request("DELETE", f"/api/projects/{project['id']}", json={"confirm": True})
    deleted.raise_for_status()
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_health_config_masks_provider_key_and_auto_run_completes() -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["runtime"] == "local"
    assert health.json()["tools"] >= 8
    tools = client.get("/api/tools").json()
    assert tools["mcp"]["enabled"] is False
    assert any(item["name"] == "segment_analysis" for item in tools["tools"])
    root = client.get("/")
    assert root.status_code == 200
    assert "风控建模 Agent" in root.text
    assert "provider-request-list" in root.text
    assert "selection-table-panel" in root.text
    assert 'name="clear_api_key"' in root.text

    project, dataset = create_demo_project("API 自动流程")
    run_response = client.post(
        f"/api/projects/{project['id']}/runs",
        json={"dataset_id": dataset["id"], "mode": "auto"},
    )
    run_response.raise_for_status()
    run = wait_for_status(run_response.json()["run"]["id"], {"succeeded", "failed", "blocked"})
    assert run["status"] == "succeeded", run.get("error")
    report = client.get(f"/api/runs/{run['id']}/report")
    assert report.status_code == 200
    assert report.json()["manifest"]["raw_data_uploaded"] is False
    report_payload = report.json()
    assert report_payload["manifest"]["protocol"] == "train_only_oof_diagnostic → validation_select → oot_once"
    assert report_payload["selection"]["funnel"]["fit_scope"] == "train"
    assert "generated_model_v1.py" in report_payload["manifest"]["artifacts"]
    assert report_payload["code_review"]["review_history"]
    assert report_payload["stability"]["schema_version"] == "risk-stability/v1"
    assert report_payload["champion"]["validation"]["calibration"]
    narrative = report_payload["narrative_sections"]["sections"]
    edited = client.post(
        f"/api/runs/{run['id']}/report/narrative",
        json={"sections": [{"id": "executive_summary", "text": "已由专家补充的执行摘要。"}], "lock": False},
    )
    edited.raise_for_status()
    assert edited.json()["report"]["narrative_sections"]["sections"][0]["source"] == "human-edited"
    locked = client.post(
        f"/api/runs/{run['id']}/report/narrative",
        json={"sections": narrative[:1], "lock": True},
    )
    locked.raise_for_status()
    rejected = client.post(
        f"/api/runs/{run['id']}/report/narrative",
        json={"sections": [{"id": "executive_summary", "text": "不应覆盖"}]},
    )
    assert rejected.status_code == 409
    run_dir = store.run_dir(project["id"], run["id"])
    checksums = json.loads((run_dir / "checksums.json").read_text(encoding="utf-8"))
    assert checksums["report.json"] == hashlib.sha256((run_dir / "report.json").read_bytes()).hexdigest()
    xlsx = client.get(f"/api/runs/{run['id']}/report.xlsx")
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"
    archive = client.get(f"/api/runs/{run['id']}/artifacts.zip")
    assert archive.status_code == 200
    assert archive.content[:2] == b"PK"
    trace = client.get(f"/api/runs/{run['id']}/trace.json")
    assert trace.status_code == 200
    trace_payload = trace.json()
    assert trace_payload["schema_version"] == "risk-trace-bundle/v1"
    assert trace_payload["manifest"]["raw_rows_included"] is False
    assert trace_payload["manifest"]["credentials_included"] is False
    assert trace_payload["manifest"]["original_column_names_included"] is False
    assert trace_payload["manifest"]["event_chain"]["valid"] is True
    assert "prior_delinquencies" not in json.dumps(trace_payload, ensure_ascii=False)
    store.record_provider_request(run["id"], "planning", "demo-model", {"evidence": {"fields": [{"alias": "f_0001"}], "raw_rows_included": False}})
    requests = client.get(f"/api/runs/{run['id']}/provider-requests")
    assert requests.status_code == 200
    assert requests.json()["requests"][0]["payload"]["evidence"]["fields"][0]["alias"] == "f_0001"
    assert all("path" not in json.dumps(event, ensure_ascii=False).lower() for event in trace_payload["events"])
    trace_archive = client.get(f"/api/runs/{run['id']}/trace.zip")
    assert trace_archive.status_code == 200
    with zipfile.ZipFile(BytesIO(trace_archive.content)) as trace_zip:
        assert trace_zip.namelist() == ["trace.json", "README.txt"]
        assert b'"raw_rows": [' not in trace_zip.read("trace.json")
    events = client.get(f"/api/runs/{run['id']}/events").json()["events"]
    assert events[-1]["event_hash"]
    assert events[-1]["payload"]["status"] == "succeeded"
    analysis = client.post(
        f"/api/projects/{project['id']}/analysis",
        json={"dataset_id": dataset["id"], "spec": {"dimensions": [{"column": "channel"}], "target": {"column": "bad_flag"}, "min_group_size": 20}},
    )
    analysis.raise_for_status()
    assert analysis.json()["analysis"]["dimensions"] == ["channel"]

    saved = client.put(
        "/api/config",
        json={"base_url": "https://api.example.test", "model": "test-model", "api_key": "secret-test-key"},
    )
    saved.raise_for_status()
    public = saved.json()["config"]
    assert public["api_key"] == "••••••••"
    assert "secret-test-key" not in saved.text
    provider_check = client.post("/api/config/test")
    assert provider_check.status_code == 200
    assert provider_check.json()["ok"] is False
    assert provider_check.json()["error_code"] == "PROVIDER_DISABLED"
    cleared = client.put("/api/config", json={"clear_api_key": True})
    cleared.raise_for_status()
    assert cleared.json()["config"]["api_key"] == ""


def test_demo_label_and_reupload_preserve_dataset_versions() -> None:
    project, demo = create_demo_project("数据版本与演示标记")
    assert demo["is_demo"] is True
    reused = client.post(f"/api/projects/{project['id']}/demo")
    reused.raise_for_status()
    assert reused.json()["dataset"]["id"] == demo["id"]
    content = b"bad_flag,income\n0,100\n1,50\n"
    for _ in range(2):
        response = client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("same.csv", BytesIO(content), "text/csv")},
        )
        response.raise_for_status()
        assert response.json()["dataset"]["is_demo"] is False
    project_view = client.get(f"/api/projects/{project['id']}").json()
    uploaded = [item for item in project_view["datasets"] if not item["is_demo"]]
    assert len(uploaded) == 2
    assert uploaded[0]["filename"] != uploaded[1]["filename"]


def test_dictionary_version_and_project_backup_exclude_raw_data_by_default() -> None:
    project, dataset = create_demo_project("字典与备份")
    dictionary = client.post(
        f"/api/projects/{project['id']}/dictionaries",
        files={"file": ("dictionary.csv", BytesIO("field,meaning\nincome,收入\n".encode()), "text/csv")},
    )
    dictionary.raise_for_status()
    assert dictionary.json()["dictionary"]["filename"] == "dictionary.csv"
    assert dictionary.json()["metadata"]["field_count"] == 1
    backup = client.get(f"/api/projects/{project['id']}/backup.zip")
    backup.raise_for_status()
    with zipfile.ZipFile(BytesIO(backup.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    assert "manifest.json" in names
    assert not any(name.startswith("datasets/") for name in names)
    assert manifest["raw_data_included"] is False
    assert manifest["dictionaries"]

    portable = client.get(f"/api/projects/{project['id']}/backup.zip?include_data=true")
    portable.raise_for_status()
    restored = client.post(
        "/api/projects/restore",
        files={"file": ("portable-backup.zip", BytesIO(portable.content), "application/zip")},
    )
    restored.raise_for_status()
    restored_payload = restored.json()
    assert restored_payload["restored_datasets"] >= 1
    restored_project = client.get(f"/api/projects/{restored_payload['project']['id']}")
    restored_project.raise_for_status()
    assert restored_project.json()["datasets"]

    metadata_only = client.get(f"/api/projects/{project['id']}/backup.zip")
    metadata_only.raise_for_status()
    restored_without_data = client.post(
        "/api/projects/restore",
        files={"file": ("metadata-backup.zip", BytesIO(metadata_only.content), "application/zip")},
    )
    restored_without_data.raise_for_status()
    assert restored_without_data.json()["missing_datasets"]


def test_project_conversation_is_stateful_and_trace_excludes_message_content() -> None:
    project, dataset = create_demo_project("多轮项目对话")
    run = store.create_run(
        project["id"],
        dataset["id"],
        "auto",
        initial_state={
            "profile": {"columns_detail": [{"name": "secret_income", "type": "numeric"}]},
            "target": {"target": "bad_flag", "contract_ok": True},
        },
        phase="planning",
    )
    store.update_run(run["id"], status="succeeded", phase="reporting", state=run["state"])
    first = client.post(
        f"/api/projects/{project['id']}/conversation",
        json={"run_id": run["id"], "message": "为什么 secret_income 需要重点复核？"},
    )
    first.raise_for_status()
    assert first.json()["assistant_message"]["structured"]["schema_version"] == "risk-chat-turn/v1"
    feedback = client.post(
        f"/api/runs/{run['id']}/feedback",
        json={"reaction": "like", "message_id": first.json()["assistant_message"]["id"]},
    )
    feedback.raise_for_status()
    second = client.post(
        f"/api/projects/{project['id']}/conversation",
        json={"run_id": run["id"], "message": "下一步应该做什么？"},
    )
    second.raise_for_status()
    history = client.get(f"/api/projects/{project['id']}/conversation?run_id={run['id']}")
    history.raise_for_status()
    assert len(history.json()["messages"]) == 4
    trace = store.trace_bundle(run["id"])
    conversation = trace["conversation"][0]
    assert all(item["content_included"] is False for item in conversation["messages"])
    assert "为什么 secret_income" not in json.dumps(trace, ensure_ascii=False)
    backup = client.get(f"/api/projects/{project['id']}/backup.zip?include_data=true")
    backup.raise_for_status()
    restored = client.post(
        "/api/projects/restore",
        files={"file": ("conversation-backup.zip", BytesIO(backup.content), "application/zip")},
    )
    restored.raise_for_status()
    assert restored.json()["restored_conversations"] >= 1


def test_xlsx_multisheet_requires_explicit_sheet() -> None:
    project, _ = create_demo_project("XLSX Sheet 预检")
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame({"bad_flag": [0, 1], "income": [100, 50]}).to_excel(writer, sheet_name="train", index=False)
        pd.DataFrame({"bad_flag": [0, 1], "income": [80, 60]}).to_excel(writer, sheet_name="oot", index=False)
    workbook.seek(0)
    content = workbook.getvalue()
    inspect = client.post(
        f"/api/projects/{project['id']}/datasets/inspect",
        files={"file": ("multi.xlsx", BytesIO(content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    inspect.raise_for_status()
    assert inspect.json()["requires_sheet"] is True
    assert inspect.json()["sheets"] == ["train", "oot"]
    rejected = client.post(
        f"/api/projects/{project['id']}/datasets",
        files={"file": ("multi.xlsx", BytesIO(content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert rejected.status_code == 400
    accepted = client.post(
        f"/api/projects/{project['id']}/datasets",
        data={"sheet": "oot"},
        files={"file": ("multi.xlsx", BytesIO(content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    accepted.raise_for_status()
    assert accepted.json()["dataset"]["sheet"] == "oot"


def test_run_pause_resume_cancel_are_persisted(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "start_run", lambda run: None)
    monkeypatch.setattr(main_module, "resume_after_pause", lambda run: None)
    project, dataset = create_demo_project("运行控制")
    created = client.post(f"/api/projects/{project['id']}/runs", json={"dataset_id": dataset["id"], "mode": "auto"})
    created.raise_for_status()
    run_id = created.json()["run"]["id"]
    paused = client.post(f"/api/runs/{run_id}/pause")
    paused.raise_for_status()
    assert paused.json()["run"]["status"] == "paused"
    resumed = client.post(f"/api/runs/{run_id}/resume")
    resumed.raise_for_status()
    assert resumed.json()["run"]["status"] == "queued"
    cancelled = client.post(f"/api/runs/{run_id}/cancel")
    cancelled.raise_for_status()
    assert cancelled.json()["run"]["status"] == "cancelled"


def test_what_if_forks_isolated_run_without_starting_formal_worker(monkeypatch) -> None:
    captured = {}

    def fake_start(run, initial_state=None, start="profile"):
        captured.update({"run": run, "state": initial_state, "start": start})

    monkeypatch.setattr(main_module, "start_run", fake_start)
    project, dataset = create_demo_project("what-if 隔离")
    frame = pd.read_csv(store.get_dataset(dataset["id"])["path"])
    profile = profile_table(frame)
    base_state = {
        "profile": profile,
        "target": target_summary(frame, "bad_flag"),
        "plan": {
            "target": "bad_flag",
            "screening": {"max_features": 50, "min_iv": 0.005, "excluded_columns": []},
            "split": {"method": "stratified_holdout"},
            "models": ["logistic_regression"],
        },
        "confirmed": True,
    }
    base = store.create_run(project["id"], dataset["id"], "auto", initial_state=base_state)
    store.update_run(base["id"], status="succeeded", phase="reporting", state=base_state)
    fork = client.post(
        f"/api/projects/{project['id']}/what-if",
        json={"base_run_id": base["id"], "changes": {"min_iv": 0.02, "max_features": 5}},
    )
    fork.raise_for_status()
    child = fork.json()["run"]
    assert child["status"] == "queued"
    assert captured["start"] == "screen"
    assert captured["run"]["id"] == child["id"]
    assert captured["state"]["run_kind"] == "experiment"
    assert captured["state"]["parent_run_id"] == base["id"]
    assert captured["state"]["plan"]["screening"]["min_iv"] == 0.02
    assert captured["state"]["plan"]["screening"]["max_features"] == 5


def test_approved_cleaning_creates_new_dataset_version(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "start_run", lambda run: None)
    project = client.post("/api/projects", json={"name": "清洗执行"}).json()["project"]
    content = b"bad_flag,income\n0,10\n1,20\n0,30\n1,40\n0,50\n1,60\n0,70\n1,80\n0,90\n1,100\n0,10\n1,20\n"
    dataset = client.post(f"/api/projects/{project['id']}/datasets", files={"file": ("dirty.csv", BytesIO(content), "text/csv")}).json()["dataset"]
    # Seed a clean-gate state so this contract test does not depend on a background worker.
    state = {"profile": {"columns_detail": [{"name": "bad_flag", "type": "numeric"}, {"name": "income", "type": "numeric"}]}, "cleaning": {"requires_confirmation": [{"code": "DUPLICATE_ROWS_REVIEW", "rows": 2}], "rule_version": "cleaning-rules/v1"}}
    run = store.create_run(project["id"], dataset["id"], "semi_trust", initial_state=state, phase="cleaning")
    store.update_run(run["id"], status="awaiting_confirmation", phase="cleaning", state=state)
    cleaned = client.post(f"/api/runs/{run['id']}/clean", json={"actions": [{"code": "DUPLICATE_ROWS_REVIEW"}]})
    cleaned.raise_for_status()
    assert cleaned.json()["execution"]["rows_removed"] == 2
    assert cleaned.json()["dataset"]["id"] != dataset["id"]
    updated = client.get(f"/api/runs/{run['id']}").json()["run"]
    assert updated["state"]["dataset_version_parent"] == dataset["id"]
    assert updated["state"]["quality"]["rows"] == 10


def test_confirmation_accepts_numeric_baseline_column(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "resume_after_confirmation", lambda run: None)
    project, dataset = create_demo_project("基线确认")
    frame = pd.read_csv(store.get_dataset(dataset["id"])["path"])
    profile = profile_table(frame)
    state = {"profile": profile, "target": target_summary(frame, "bad_flag"), "plan": {"target": "bad_flag", "split": {"method": "stratified_holdout"}, "models": ["logistic_regression"], "screening": {}}}
    run = store.create_run(project["id"], dataset["id"], "semi_trust", initial_state=state, phase="cleaning")
    store.update_run(run["id"], status="awaiting_confirmation", phase="cleaning", state=state)
    confirmed = client.post(f"/api/runs/{run['id']}/decision", json={"kind": "plan_confirmation", "values": {"target": "bad_flag", "split_method": "stratified_holdout", "models": ["logistic_regression"], "baseline_column": "income"}})
    confirmed.raise_for_status()
    assert confirmed.json()["run"]["state"]["plan"]["baseline_column"] == "income"


def test_baseline_reevaluation_writes_new_oot_artifact() -> None:
    project, dataset = create_demo_project("既有模型新 OOT 复评")
    report = {
        "title": "测试报告",
        "narrative": "测试",
        "metrics": [],
        "champion": {},
        "selection": {"decisions": []},
        "plan": {"target": "bad_flag"},
        "profile": {},
        "quality": {},
        "cleaning": {},
        "manifest": {"run_id": "pending", "dataset_id": dataset["id"], "artifacts": []},
        "baseline": {"score_column": "existing_score", "orientation": "higher_is_bad", "validation": {"threshold": 0.5}},
        "narrative_sections": {"sections": [], "locked": False},
    }
    state = {"plan": {"target": "bad_flag"}, "report": report}
    run = store.create_run(project["id"], dataset["id"], "auto", initial_state=state, phase="reporting")
    report["manifest"]["run_id"] = run["id"]
    store.update_run(run["id"], status="succeeded", phase="reporting", state={**state, "report": report})
    run_dir = store.run_dir(project["id"], run["id"])
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    content = b"bad_flag,existing_score\n0,0.1\n1,0.9\n0,0.2\n1,0.8\n0,0.3\n1,0.7\n"
    oot = client.post(f"/api/projects/{project['id']}/datasets", files={"file": ("new_oot.csv", BytesIO(content), "text/csv")})
    oot.raise_for_status()
    response = client.post(
        f"/api/runs/{run['id']}/baseline/reevaluate",
        json={"dataset_id": oot.json()["dataset"]["id"], "score_column": "existing_score", "approval_rate": 0.8},
    )
    response.raise_for_status()
    reevaluation = response.json()["reevaluation"]
    assert reevaluation["schema_version"] == "risk-baseline-reevaluation/v1"
    assert reevaluation["eval_scope"] == "new_oot_only"
    assert response.json()["artifact"].startswith("baseline-reevaluation-")


def test_semi_trust_run_waits_for_decision_then_completes() -> None:
    project, dataset = create_demo_project("API 半信任流程")
    run_response = client.post(
        f"/api/projects/{project['id']}/runs",
        json={"dataset_id": dataset["id"], "mode": "semi_trust"},
    )
    run_response.raise_for_status()
    run_id = run_response.json()["run"]["id"]
    waiting = wait_for_status(run_id, {"awaiting_confirmation", "failed", "blocked"})
    assert waiting["status"] == "awaiting_confirmation", waiting.get("error")
    assert waiting["phase"] == "cleaning"
    assert waiting["state"].get("quality", {}).get("schema_version") == "risk-eda/v1"
    assert waiting["state"].get("cleaning", {}).get("schema_version") == "risk-cleaning-plan/v1"
    if waiting["state"].get("cleaning", {}).get("requires_confirmation"):
        bypass = client.post(
            f"/api/runs/{run_id}/decision",
            json={"kind": "confirm_plan", "values": {"target": "bad_flag", "split_method": "time_holdout", "models": ["logistic_regression"], "confirmed": True}},
        )
        assert bypass.status_code == 409
        skipped = client.post(f"/api/runs/{run_id}/clean", json={"actions": []})
        skipped.raise_for_status()
        assert skipped.json()["execution"]["status"] == "skipped"
    decision = client.post(
        f"/api/runs/{run_id}/decision",
        json={"kind": "confirm_plan", "values": {"target": "bad_flag", "split_method": "time_holdout", "models": ["logistic_regression", "random_forest"], "confirmed": True}},
    )
    decision.raise_for_status()
    finished = wait_for_status(run_id, {"succeeded", "failed", "blocked"})
    assert finished["status"] == "succeeded", finished.get("error")
    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["plan"]["models"] == ["logistic_regression", "random_forest"]
    assert report["split"]["method"] == "time_holdout"
