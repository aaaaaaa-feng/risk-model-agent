from __future__ import annotations

import asyncio
import sqlite3

import pytest

from app.api.conversations import list_conversation_events
from app.api.runs import list_events, stream_events
from app.core.database import Database, SCHEMA_VERSION, now_iso


def test_database_context_closes_handle_and_preserves_transaction(context):
    with context.database.connect() as connection:
        connection.execute("INSERT INTO schema_meta(key,value) VALUES('audit_test','committed')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with context.database.connect() as check:
        assert (
            check.execute("SELECT value FROM schema_meta WHERE key='audit_test'").fetchone()[0]
            == "committed"
        )


def test_newer_database_schema_is_rejected_without_rewriting_version(app_paths):
    database = Database(paths=app_paths)
    with database.connect() as connection:
        connection.execute(
            "UPDATE schema_meta SET value=? WHERE key='version'", (str(SCHEMA_VERSION + 1),)
        )
    with pytest.raises(ValueError, match="DATABASE_SCHEMA_NEWER_THAN_APPLICATION"):
        Database(paths=app_paths)
    with sqlite3.connect(app_paths.database) as connection:
        assert connection.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[
            0
        ] == str(SCHEMA_VERSION + 1)


def test_event_polling_and_stream_resume_beyond_first_5000(context):
    project = context.catalog.create_project("长事件流")
    run_id = "run_event_boundary"
    created = now_iso()
    context.database.insert(
        "runs",
        {
            "id": run_id,
            "project_id": project["id"],
            "status": "succeeded",
            "seq": 5002,
            "created_at": created,
            "updated_at": created,
        },
    )
    conversation = context.catalog.ensure_conversation(project["id"])
    with context.database.transaction() as connection:
        connection.executemany(
            "INSERT INTO events (id,run_id,seq,stage,node,agent,status,summary,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"evt_{i}",
                    run_id,
                    i,
                    "completed",
                    "complete",
                    "orchestrator",
                    "succeeded",
                    "audit",
                    created,
                )
                for i in range(1, 5003)
            ],
        )
        connection.executemany(
            "INSERT INTO conversation_events (id,conversation_id,seq,status,agent,created_at) VALUES(?,?,?,?,?,?)",
            [
                (f"cevt_{i}", conversation["id"], i, "completed", "main_agent", created)
                for i in range(1, 5003)
            ],
        )
    events = list_events(run_id, after=5000, ctx=context)
    assert [event["sequence"] for event in events["events"]] == [5001, 5002]
    chat_events = list_conversation_events(conversation["id"], after=5000, ctx=context)
    assert [event["sequence"] for event in chat_events["events"]] == [5001, 5002]

    async def collect():
        response = await stream_events(run_id, after=5000, last_event_id=None, ctx=context)
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(asyncio.wait_for(collect(), timeout=2))
    assert len(chunks) == 3
    assert "event: stream_end" in chunks[-1]
