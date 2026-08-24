from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.providers.gateway import ProviderResult


def _wait_for_response(context, conversation_id: str, response_id: str) -> list[dict]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = context.database.list(
            "conversation_events",
            {"conversation_id": conversation_id},
            order_by="seq ASC",
            limit=5000,
        )
        if any(
            item["status"] == "completed"
            and item.get("evidence", {}).get("response_id") == response_id
            for item in events
        ):
            return events
        time.sleep(0.02)
    raise AssertionError(f"Conversation response {response_id} did not complete")


def test_conversation_has_one_authoritative_response_and_stream_is_response_scoped(
    app_paths, monkeypatch
):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "对话来源测试"}).json()["project"]

        fallback = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={"content": "第一次问题"},
        ).json()
        _wait_for_response(
            app.state.context,
            fallback["conversation_id"],
            fallback["response_id"],
        )
        messages = client.get(f"/api/v1/projects/{project['id']}/conversation").json()["messages"]
        first_answer = [item for item in messages if item["role"] == "assistant"][-1]
        assert first_answer["content"].startswith("API 未连接")
        assert "仅基于本地项目状态" in first_answer["content"]

        class ConnectedGateway:
            enabled = True
            configured = True
            key = "configured-but-never-rendered"

            def __init__(self, **_kwargs):
                pass

            def complete(self, *_args, **_kwargs):
                return ProviderResult(ok=True, content="这是本次唯一的 LLM 答复。")

        monkeypatch.setattr(
            "app.services.conversations.ProviderGateway",
            ConnectedGateway,
        )
        provider = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={"content": "第二次问题"},
        ).json()
        events = _wait_for_response(
            app.state.context,
            provider["conversation_id"],
            provider["response_id"],
        )

        streamed = client.get(
            f"/api/v1/conversations/{provider['conversation_id']}/events/stream",
            params={"response_id": provider["response_id"]},
        )
        assert streamed.status_code == 200
        payloads = []
        for line in streamed.text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line.removeprefix("data: "))
            if "status" in payload:
                payloads.append(payload)
        assert payloads
        assert {item["evidence"]["response_id"] for item in payloads} == {provider["response_id"]}
        assert (
            "".join(item["content"] for item in payloads if item["status"] == "delta")
            == "这是本次唯一的 LLM 答复。"
        )
        assert "API 未连接" not in streamed.text

        completed = next(
            item
            for item in events
            if item["status"] == "completed"
            and item.get("evidence", {}).get("response_id") == provider["response_id"]
        )
        assert completed["evidence"]["response_source"] == "provider"
        messages = client.get(f"/api/v1/projects/{project['id']}/conversation").json()["messages"]
        assert [item["content"] for item in messages if item["role"] == "assistant"][-1] == (
            "这是本次唯一的 LLM 答复。"
        )


def test_response_stream_bypasses_history_cap_and_recovers_completed_reconnect(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "长对话流"}).json()["project"]
        context = app.state.context
        conversation = context.catalog.ensure_conversation(project["id"])
        old_evidence = json.dumps({"response_id": "response_old"})
        target_id = "response_after_5000"
        target_evidence = json.dumps({"response_id": target_id})
        rows = [
            (
                f"cevt_old_{sequence}",
                conversation["id"],
                sequence,
                "delta",
                "main_agent",
                "旧",
                "",
                old_evidence,
                "2026-08-24T00:00:00+00:00",
            )
            for sequence in range(1, 5002)
        ]
        rows.extend(
            [
                (
                    "cevt_target_queued",
                    conversation["id"],
                    5002,
                    "queued",
                    "main_agent",
                    "",
                    "已排队",
                    target_evidence,
                    "2026-08-24T00:00:01+00:00",
                ),
                (
                    "cevt_target_delta",
                    conversation["id"],
                    5003,
                    "delta",
                    "main_agent",
                    "跨过上限后的答复",
                    "",
                    target_evidence,
                    "2026-08-24T00:00:02+00:00",
                ),
                (
                    "cevt_target_completed",
                    conversation["id"],
                    5004,
                    "completed",
                    "main_agent",
                    "",
                    "答复完成",
                    target_evidence,
                    "2026-08-24T00:00:03+00:00",
                ),
            ]
        )
        with context.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO conversation_events
                (id, conversation_id, seq, status, agent, content, summary,
                 evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

        streamed = client.get(
            f"/api/v1/conversations/{conversation['id']}/events/stream",
            params={"response_id": target_id},
        )
        assert streamed.status_code == 200
        assert "跨过上限后的答复" in streamed.text
        assert "cevt_old_" not in streamed.text

        reconnected = client.get(
            f"/api/v1/conversations/{conversation['id']}/events/stream",
            params={"response_id": target_id},
            headers={"Last-Event-ID": "5004"},
        )
        assert reconnected.status_code == 200
        assert "event: stream_end" in reconnected.text
        assert '"recovered": true' in reconnected.text
        assert "event: conversation_event" not in reconnected.text
