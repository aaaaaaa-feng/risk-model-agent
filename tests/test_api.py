from __future__ import annotations

import time
from io import BytesIO

from fastapi.testclient import TestClient

from app.main import app


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


def test_health_config_masks_provider_key_and_auto_run_completes() -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["runtime"] == "local"
    root = client.get("/")
    assert root.status_code == 200
    assert "风控建模 Agent" in root.text

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
    events = client.get(f"/api/runs/{run['id']}/events").json()["events"]
    assert events[-1]["event_hash"]
    assert events[-1]["payload"]["status"] == "succeeded"

    saved = client.put(
        "/api/config",
        json={"base_url": "https://api.example.test", "model": "test-model", "api_key": "secret-test-key"},
    )
    saved.raise_for_status()
    public = saved.json()["config"]
    assert public["api_key"] == "••••••••"
    assert "secret-test-key" not in saved.text


def test_demo_label_and_reupload_preserve_dataset_versions() -> None:
    project, demo = create_demo_project("数据版本与演示标记")
    assert demo["is_demo"] is True
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
    decision = client.post(
        f"/api/runs/{run_id}/decision",
        json={"kind": "confirm_plan", "values": {"target": "bad_flag", "confirmed": True}},
    )
    decision.raise_for_status()
    finished = wait_for_status(run_id, {"succeeded", "failed", "blocked"})
    assert finished["status"] == "succeeded", finished.get("error")
