from __future__ import annotations

import json
import threading
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


def _insert_run(context, project_id: str, run_id: str, stage: str, created_at: str) -> dict:
    return context.database.insert(
        "runs",
        {
            "id": run_id,
            "project_id": project_id,
            "target_task_id": None,
            "status": "awaiting_decision",
            "stage": stage,
            "node": f"node_{stage}",
            "mode": "semi_trusted",
            "seq": 1,
            "progress": 0.5,
            "state_json": {},
            "error": None,
            "started_at": created_at,
            "finished_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        },
    )


def _hold_conversation_workers(service):
    ready = threading.Barrier(3)
    release = threading.Event()

    def hold() -> None:
        ready.wait(timeout=5)
        release.wait(timeout=10)

    futures = [service._executor.submit(hold) for _ in range(2)]
    ready.wait(timeout=5)
    return release, futures


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


def test_conversation_uses_the_explicitly_selected_run_and_decision(app_paths, monkeypatch):
    captured: dict = {}

    class ConnectedGateway:
        enabled = True
        configured = True
        key = "configured-but-never-rendered"

        def __init__(self, **_kwargs):
            pass

        def complete(self, _prompt, payload, **_kwargs):
            captured.update(payload)
            return ProviderResult(ok=True, content="已按用户正在查看的历史 Run 回答。")

    monkeypatch.setattr("app.services.conversations.ProviderGateway", ConnectedGateway)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "显式对话上下文"}).json()["project"]
        context = app.state.context
        selected = _insert_run(
            context,
            project["id"],
            "run_selected",
            "feature_screening",
            "2026-08-24T00:00:00+00:00",
        )
        _insert_run(
            context,
            project["id"],
            "run_latest",
            "training",
            "2026-08-25T00:00:00+00:00",
        )
        decision = context.database.insert(
            "decisions",
            {
                "id": "decision_selected",
                "run_id": selected["id"],
                "stage": selected["stage"],
                "kind": "confirm_screening",
                "status": "pending",
                "payload_json": {},
                "review_json": {},
                "created_at": "2026-08-24T00:01:00+00:00",
                "resolved_at": None,
            },
        )

        sent = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={
                "content": "解释当前阶段",
                "context": {
                    "run_id": selected["id"],
                    "stage": "stale_frontend_stage",
                    "decision_id": decision["id"],
                },
            },
        )
        assert sent.status_code == 202
        response = sent.json()
        assert response["context"] == {
            "selection": "explicit",
            "run_id": selected["id"],
            "stage": selected["stage"],
            "decision_id": decision["id"],
            "decision_kind": decision["kind"],
            "decision_is_current": True,
            "stage_changed": True,
        }
        _wait_for_response(context, response["conversation_id"], response["response_id"])

    assert captured["current_run"] == {
        "selected_explicitly": True,
        "status": "awaiting_decision",
        "stage": "feature_screening",
        "node": "node_feature_screening",
        "progress": 0.5,
    }
    assert captured["current_decision"] == {
        "kind": "confirm_screening",
        "stage": "feature_screening",
        "status": "pending",
        "is_current": True,
    }
    assert captured["context_stage_changed"] is True


def test_conversation_freezes_causal_history_per_request(app_paths, monkeypatch):
    captured: list[dict] = []
    captured_lock = threading.Lock()

    class ConnectedGateway:
        enabled = True
        configured = True
        key = "configured-but-never-rendered"

        def __init__(self, **_kwargs):
            pass

        def complete(self, _prompt, payload, **_kwargs):
            with captured_lock:
                captured.append(payload)
            return ProviderResult(ok=True, content="已按冻结的对话轮次回答。")

    monkeypatch.setattr("app.services.conversations.ProviderGateway", ConnectedGateway)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "并发对话上下文"}).json()["project"]
        context = app.state.context
        run_a = _insert_run(
            context,
            project["id"],
            "run_history_a",
            "binning",
            "2026-08-24T00:00:00+00:00",
        )
        run_b = _insert_run(
            context,
            project["id"],
            "run_history_b",
            "training",
            "2026-08-24T00:00:01+00:00",
        )
        release, futures = _hold_conversation_workers(context.conversations)
        try:
            first = client.post(
                f"/api/v1/projects/{project['id']}/conversation/messages",
                json={"content": "问题A：解释分箱", "context": {"run_id": run_a["id"]}},
            ).json()
            second = client.post(
                f"/api/v1/projects/{project['id']}/conversation/messages",
                json={"content": "问题B：解释训练", "context": {"run_id": run_b["id"]}},
            ).json()
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
        _wait_for_response(context, first["conversation_id"], first["response_id"])
        _wait_for_response(context, second["conversation_id"], second["response_id"])

    by_stage = {payload["current_run"]["stage"]: payload for payload in captured}
    first_history = by_stage["binning"]["conversation_history"]
    second_history = by_stage["training"]["conversation_history"]
    assert all("问题B" not in item["content"] for item in first_history)
    assert any("问题A" in item["content"] for item in second_history)


def test_conversation_uses_one_run_snapshot_when_background_answer_starts_later(
    app_paths, monkeypatch
):
    captured: dict = {}

    class ConnectedGateway:
        enabled = True
        configured = True
        key = "configured-but-never-rendered"

        def __init__(self, **_kwargs):
            pass

        def complete(self, _prompt, payload, **_kwargs):
            captured.update(payload)
            return ProviderResult(ok=True, content="已按提问时的阶段快照回答。")

    monkeypatch.setattr("app.services.conversations.ProviderGateway", ConnectedGateway)
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "阶段快照"}).json()["project"]
        context = app.state.context
        run = _insert_run(
            context,
            project["id"],
            "run_snapshot",
            "binning",
            "2026-08-24T00:00:00+00:00",
        )
        release, futures = _hold_conversation_workers(context.conversations)
        try:
            sent = client.post(
                f"/api/v1/projects/{project['id']}/conversation/messages",
                json={
                    "content": "解释当前阶段",
                    "context": {"run_id": run["id"], "stage": "binning"},
                },
            ).json()
            context.database.update(
                "runs",
                run["id"],
                {
                    "stage": "training",
                    "node": "node_training",
                    "updated_at": "2026-08-24T00:01:00+00:00",
                },
            )
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)
        events = _wait_for_response(context, sent["conversation_id"], sent["response_id"])

    completed = next(
        item
        for item in events
        if item["status"] == "completed"
        and item.get("evidence", {}).get("response_id") == sent["response_id"]
    )
    assert captured["current_run"]["stage"] == "binning"
    assert captured["context_stage_changed"] is False
    assert completed["evidence"]["context"]["stage"] == "binning"
    assert completed["evidence"]["context"]["stage_changed"] is False
    assert completed["evidence"]["user_message_id"] == sent["user_message"]["id"]


def test_run_only_conversation_context_resolves_the_pending_decision(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "待确认上下文"}).json()["project"]
        context = app.state.context
        run = _insert_run(
            context,
            project["id"],
            "run_pending_decision",
            "binning",
            "2026-08-24T00:00:00+00:00",
        )
        decision = context.database.insert(
            "decisions",
            {
                "id": "decision_inferred",
                "run_id": run["id"],
                "stage": "binning",
                "kind": "confirm_binning",
                "status": "pending",
                "payload_json": {},
                "review_json": {},
                "created_at": "2026-08-24T00:01:00+00:00",
                "resolved_at": None,
            },
        )
        context.database.insert(
            "decisions",
            {
                "id": "decision_stale_but_newer",
                "run_id": run["id"],
                "stage": "feature_screening",
                "kind": "confirm_screening",
                "status": "pending",
                "payload_json": {},
                "review_json": {},
                "created_at": "2026-08-24T00:02:00+00:00",
                "resolved_at": None,
            },
        )

        sent = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={"content": "解释当前确认", "context": {"run_id": run["id"]}},
        )
        assert sent.status_code == 202
        assert sent.json()["context"]["decision_id"] == decision["id"]
        assert sent.json()["context"]["decision_is_current"] is True


def test_pending_decision_is_not_current_after_run_leaves_the_confirmation_gate(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "已离开确认节点"}).json()["project"]
        context = app.state.context
        run = _insert_run(
            context,
            project["id"],
            "run_after_confirmation",
            "binning",
            "2026-08-24T00:00:00+00:00",
        )
        decision = context.database.insert(
            "decisions",
            {
                "id": "decision_left_behind",
                "run_id": run["id"],
                "stage": "binning",
                "kind": "confirm_binning",
                "status": "pending",
                "payload_json": {},
                "review_json": {},
                "created_at": "2026-08-24T00:01:00+00:00",
                "resolved_at": None,
            },
        )
        context.database.update(
            "runs",
            run["id"],
            {
                "status": "succeeded",
                "progress": 1.0,
                "finished_at": "2026-08-24T00:02:00+00:00",
                "updated_at": "2026-08-24T00:02:00+00:00",
            },
        )

        sent = client.post(
            f"/api/v1/projects/{project['id']}/conversation/messages",
            json={"content": "还有待确认项吗？", "context": {"run_id": run["id"]}},
        )

        assert sent.status_code == 202
        assert sent.json()["context"]["decision_id"] == decision["id"]
        assert sent.json()["context"]["decision_is_current"] is False


def test_conversation_rejects_cross_project_or_mismatched_context(app_paths):
    app = create_app(app_paths, auto_migrate=False)
    with TestClient(app) as client:
        first = client.post("/api/v1/projects", json={"name": "项目一"}).json()["project"]
        second = client.post("/api/v1/projects", json={"name": "项目二"}).json()["project"]
        context = app.state.context
        first_run = _insert_run(
            context,
            first["id"],
            "run_first",
            "binning",
            "2026-08-24T00:00:00+00:00",
        )
        second_run = _insert_run(
            context,
            second["id"],
            "run_second",
            "training",
            "2026-08-24T00:00:01+00:00",
        )
        decision = context.database.insert(
            "decisions",
            {
                "id": "decision_first",
                "run_id": first_run["id"],
                "stage": "binning",
                "kind": "confirm_binning",
                "status": "pending",
                "payload_json": {},
                "review_json": {},
                "created_at": "2026-08-24T00:01:00+00:00",
                "resolved_at": None,
            },
        )

        cross_project = client.post(
            f"/api/v1/projects/{first['id']}/conversation/messages",
            json={"content": "当前是什么？", "context": {"run_id": second_run["id"]}},
        )
        assert cross_project.status_code == 400
        assert cross_project.json()["error"]["code"] == "CONVERSATION_RUN_PROJECT_MISMATCH"

        mismatched_decision = client.post(
            f"/api/v1/projects/{second['id']}/conversation/messages",
            json={
                "content": "解释确认节点",
                "context": {
                    "run_id": second_run["id"],
                    "decision_id": decision["id"],
                },
            },
        )
        assert mismatched_decision.status_code == 400
        assert mismatched_decision.json()["error"]["code"] == ("CONVERSATION_DECISION_RUN_MISMATCH")


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
