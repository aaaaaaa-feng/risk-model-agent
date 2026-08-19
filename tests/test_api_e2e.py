from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.sample_data import SAMPLE_FILENAME, generate_sample_csv


def _settings(tmp_path: Path) -> Settings:
    instance = tmp_path / "instance"
    return Settings(
        instance_dir=instance,
        database_path=instance / "test.sqlite3",
        max_upload_bytes=5 * 1024 * 1024,
        max_rows=5_000,
    )


def test_upload_approval_training_report_and_agent_flow(tmp_path):
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["external_data_egress"] is False

        created_response = client.post(
            "/api/projects",
            data={"name": "端到端框架测试"},
            files={"file": (SAMPLE_FILENAME, generate_sample_csv(360), "text/csv")},
        )
        assert created_response.status_code == 201, created_response.text
        project = created_response.json()
        project_id = project["id"]
        assert project["status"] == "profiled"
        assert project["dataset"]["is_demo"] is True
        assert project["profile"]["row_count"] == 360
        assert any(item["column"] == "bad_flag" for item in project["profile"]["binary_candidates"])
        assert "rows" not in project["profile"]

        early_train = client.post(f"/api/projects/{project_id}/train", json={})
        assert early_train.status_code == 409
        assert early_train.json()["error"]["code"] == "PLAN_NOT_APPROVED"

        plan_payload = {
            "target_column": "bad_flag",
            "positive_label": 1,
            "time_column": None,
            "excluded_columns": [],
        }
        planned_response = client.post(f"/api/projects/{project_id}/plan", json=plan_payload)
        assert planned_response.status_code == 200, planned_response.text
        project = planned_response.json()
        plan = project["plan"]
        assert project["status"] == "awaiting_approval"
        assert plan["blocking_issues"] == []
        assert "application_id" in plan["features"]["dropped_columns"]
        assert plan["split"]["method"] == "stratified_random"

        incomplete = client.post(
            f"/api/projects/{project_id}/approve",
            json={
                "plan_version": plan["version"],
                "plan_hash": plan["plan_hash"],
                "confirmations": ["target_definition"],
            },
        )
        assert incomplete.status_code == 422
        assert incomplete.json()["error"]["code"] == "CONFIRMATIONS_INCOMPLETE"

        approved_response = client.post(
            f"/api/projects/{project_id}/approve",
            json={
                "plan_version": plan["version"],
                "plan_hash": plan["plan_hash"],
                "confirmations": plan["required_confirmations"],
            },
        )
        assert approved_response.status_code == 200, approved_response.text
        project = approved_response.json()
        assert project["status"] == "approved"

        # Regenerating an identical plan must preserve the existing confirmation.
        unchanged_response = client.post(f"/api/projects/{project_id}/plan", json=plan_payload)
        assert unchanged_response.status_code == 200
        unchanged = unchanged_response.json()
        assert unchanged["status"] == "approved"
        assert unchanged["approval"]["plan_hash"] == plan["plan_hash"]

        trained_response = client.post(f"/api/projects/{project_id}/train", json={})
        assert trained_response.status_code == 200, trained_response.text
        project = trained_response.json()
        assert project["status"] == "completed"
        assert len(project["runs"]) == 1
        run = project["latest_run"]
        result = run["result"]
        assert run["status"] == "completed"
        assert result["dataset_is_demo"] is True
        assert result["plan_hash"] == plan["plan_hash"]
        assert 0.0 <= result["holdout_metrics"]["roc_auc"] <= 1.0
        assert len(result["candidate_comparison"]) == 3
        assert "predictions" not in result

        report = client.get(run["report_url"])
        assert report.status_code == 200
        assert "演示数据" in report.text
        assert "不代表未来业务表现" in report.text

        agent = client.post(f"/api/projects/{project_id}/agent", json={"message": "结果怎么样？"})
        assert agent.status_code == 200
        answer = agent.json()
        assert answer["mode"] == "deterministic_offline_assistant"
        assert any(section["kind"] == "fact" for section in answer["sections"])
        assert "不作授信决定" in answer["boundary"]


def test_changed_plan_invalidates_old_approval(tmp_path):
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        project = client.post(
            "/api/projects",
            data={"name": "批准失效测试"},
            files={"file": (SAMPLE_FILENAME, generate_sample_csv(240), "text/csv")},
        ).json()
        project_id = project["id"]
        base_payload = {
            "target_column": "bad_flag",
            "positive_label": 1,
            "time_column": None,
            "excluded_columns": [],
        }
        planned = client.post(f"/api/projects/{project_id}/plan", json=base_payload).json()
        plan = planned["plan"]
        approved = client.post(
            f"/api/projects/{project_id}/approve",
            json={
                "plan_version": plan["version"],
                "plan_hash": plan["plan_hash"],
                "confirmations": plan["required_confirmations"],
            },
        )
        assert approved.status_code == 200

        changed_payload = dict(base_payload)
        changed_payload["excluded_columns"] = ["age"]
        changed = client.post(f"/api/projects/{project_id}/plan", json=changed_payload).json()
        assert changed["status"] == "awaiting_approval"
        assert changed["approval"] is None
        assert changed["plan"]["version"] == plan["version"] + 1
        assert changed["plan"]["plan_hash"] != plan["plan_hash"]
