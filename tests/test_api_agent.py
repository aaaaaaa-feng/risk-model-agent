from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import wait_for_run


def test_api_v1_health_and_demo_project(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["raw_data_cloud_upload"] is False
        assert health.json()["mcp"]["enabled"] is False
        response = client.post(
            "/api/v1/projects/demo",
            json={"mode": "semi_trusted", "rows": 600, "seed": 20260821},
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["synthetic_evidence"]["is_synthetic"] is True
        assert len(payload["target_tasks"]) == 3
        detail = client.get(f"/api/v1/projects/{payload['project']['id']}").json()
        assert len(detail["assets"]) == 5
        assert len(detail["dataset_versions"]) == 1


def test_semi_trusted_interrupt_resume_and_reject(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        demo = client.post(
            "/api/v1/projects/demo",
            json={"mode": "semi_trusted", "rows": 600},
        ).json()
        created = client.post(
            "/api/v1/runs",
            json={
                "project_id": demo["project"]["id"],
                "target_task_id": demo["target_tasks"][0]["id"],
                "mode": "semi_trusted",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run"]["id"]
        context = app.state.context
        first = wait_for_run(context, run_id, {"awaiting_decision", "failed"}, 30)
        assert first["status"] == "awaiting_decision"
        pending = client.get(f"/api/v1/runs/{run_id}").json()["pending_decisions"]
        assert len(pending) == 1
        assert pending[0]["stage"] == "target_confirmation"
        assert pending[0]["review"]["status"] == "pass"
        approved = client.post(
            f"/api/v1/runs/{run_id}/decisions/{pending[0]['id']}",
            json={"approved": True, "edits": {}},
        )
        assert approved.status_code == 202
        second = wait_for_run(context, run_id, {"awaiting_decision", "failed"}, 30)
        assert second["status"] == "awaiting_decision"
        deadline = time.monotonic() + 5
        next_pending = []
        while time.monotonic() < deadline and not next_pending:
            next_pending = client.get(f"/api/v1/runs/{run_id}").json()["pending_decisions"]
            if not next_pending:
                time.sleep(0.02)
        assert next_pending[0]["stage"] == "data_diagnosis"
        rejected = client.post(
            f"/api/v1/runs/{run_id}/decisions/{next_pending[0]['id']}",
            json={"approved": False, "edits": {}},
        )
        assert rejected.status_code == 202
        final = wait_for_run(context, run_id, {"blocked", "failed"}, 30)
        assert final["status"] == "blocked"
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
        assert all(
            {"sequence", "stage", "node", "agent", "tool", "status", "summary", "time", "evidence"}.issubset(event)
            for event in events
        )
        assert all(event["hidden_chain_of_thought_included"] is False for event in events)


def test_provider_settings_clear_priority_and_presets(app_paths, monkeypatch):
    monkeypatch.delenv("RISK_AGENT_API_KEY", raising=False)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        presets = client.get("/api/v1/providers/presets").json()["presets"]
        assert presets["kimi-code"]["model"] == "kimi-for-coding"
        saved = client.put(
            "/api/v1/providers/settings",
            json={"provider": "openai", "api_key": "temporary-key-value", "llm_enabled": True},
        ).json()["settings"]
        assert saved["api_key_configured"] is True
        cleared = client.put(
            "/api/v1/providers/settings",
            json={"api_key": "must-not-win", "clear_api_key": True},
        ).json()["settings"]
        assert cleared["api_key_configured"] is False


def test_conversation_is_persistent_multiturn_with_feedback(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "对话项目"}).json()["project"]
        sent = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={"content": "当前项目下一步是什么？"},
        )
        assert sent.status_code == 202
        context = app.state.context
        conversation = context.catalog.ensure_conversation(project["id"])
        deadline = time.monotonic() + 10
        messages = []
        while time.monotonic() < deadline:
            messages = context.database.list(
                "conversation_messages", {"conversation_id": conversation["id"]}, limit=20
            )
            if any(item["role"] == "assistant" for item in messages):
                break
            time.sleep(0.05)
        assistant = next(item for item in messages if item["role"] == "assistant")
        feedback = client.post(
            f"/api/v1/conversation-messages/{assistant['id']}/feedback",
            json={"rating": "up", "reason": "有帮助"},
        )
        assert feedback.status_code == 201
