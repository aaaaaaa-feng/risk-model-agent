from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.database import new_id, now_iso
from app.bootstrap import AppContext

from .dependencies import context


router = APIRouter(tags=["agent-conversation"])
EVENT_BATCH_SIZE = 5000


class ConversationContextPayload(BaseModel):
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    stage: str | None = Field(default=None, min_length=1, max_length=120)
    decision_id: str | None = Field(default=None, min_length=1, max_length=160)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    context: ConversationContextPayload | None = None


class FeedbackCreate(BaseModel):
    rating: str
    reason: str = Field(default="", max_length=2000)


@router.get("/projects/{project_id}/conversation")
def get_conversation(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    conversation = ctx.catalog.ensure_conversation(project_id)
    messages = ctx.database.list(
        "conversation_messages",
        {"conversation_id": conversation["id"]},
        order_by="created_at ASC",
        limit=5000,
    )
    return {"conversation": conversation, "messages": messages}


@router.post("/projects/{project_id}/conversation/messages", status_code=202)
def send_message(
    project_id: str,
    payload: SendMessage,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    return ctx.conversations.send(
        project_id,
        payload.content,
        payload.context.model_dump() if payload.context else None,
    )


@router.post("/conversation-messages/{message_id}/feedback", status_code=201)
def add_feedback(
    message_id: str,
    payload: FeedbackCreate,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    if payload.rating not in {"up", "down"}:
        raise ValueError("FEEDBACK_RATING_INVALID")
    ctx.catalog.require("conversation_messages", message_id)
    record = ctx.database.insert(
        "message_feedback",
        {
            "id": new_id("feedback"),
            "message_id": message_id,
            "rating": payload.rating,
            "reason": payload.reason,
            "created_at": now_iso(),
        },
    )
    return {"feedback": record}


@router.get("/conversations/{conversation_id}/events")
def list_conversation_events(
    conversation_id: str,
    after: int = 0,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    ctx.catalog.require("conversations", conversation_id)
    values = [
        _event(item)
        for item in ctx.database.list(
            "conversation_events",
            {"conversation_id": conversation_id},
            order_by="seq ASC",
            limit=5000,
        )
        if int(item["seq"]) > after
    ]
    return {"events": values, "next_sequence": values[-1]["sequence"] if values else after}


@router.get("/conversations/{conversation_id}/events/stream")
async def stream_conversation_events(
    conversation_id: str,
    after: int = 0,
    response_id: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ctx: AppContext = Depends(context),
) -> StreamingResponse:
    ctx.catalog.require("conversations", conversation_id)
    cursor = after
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError:
            raise HTTPException(400, detail={"code": "EVENT_CURSOR_INVALID"})

    async def generate() -> AsyncIterator[str]:
        current = cursor
        quiet_ticks = 0
        if response_id is not None and current > 0:
            completed_sequence = _completed_response_sequence(
                ctx,
                conversation_id,
                response_id,
                current,
            )
            if completed_sequence is not None:
                yield _stream_end(
                    conversation_id,
                    completed_sequence,
                    recovered=True,
                )
                return
        while True:
            values = _events_after(
                ctx,
                conversation_id,
                current,
                response_id,
            )
            for item in values:
                payload = _event(item)
                current = payload["sequence"]
                # A new EventSource starts at sequence zero.  Without this
                # response boundary it would replay old deltas (often a local
                # fallback) before streaming the current LLM answer.
                yield f"id: {current}\nevent: conversation_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload["status"] == "completed":
                    yield _stream_end(conversation_id, current)
                    return
                quiet_ticks = 0
            if len(values) == EVENT_BATCH_SIZE:
                continue
            quiet_ticks += 1
            if quiet_ticks >= 30:
                yield f": keepalive {current}\n\n"
                quiet_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _events_after(
    ctx: AppContext,
    conversation_id: str,
    after: int,
    response_id: str | None,
) -> list[dict[str, Any]]:
    where = "conversation_id=? AND seq>?"
    parameters: list[Any] = [conversation_id, after]
    if response_id is not None:
        where += " AND json_valid(evidence_json) AND json_extract(evidence_json, '$.response_id')=?"
        parameters.append(response_id)
    parameters.append(EVENT_BATCH_SIZE)
    with ctx.database.connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM conversation_events WHERE {where} ORDER BY seq ASC LIMIT ?",
            tuple(parameters),
        ).fetchall()
    return [ctx.database._decode(row) or {} for row in rows]


def _completed_response_sequence(
    ctx: AppContext,
    conversation_id: str,
    response_id: str,
    at_or_before: int,
) -> int | None:
    with ctx.database.connect() as connection:
        row = connection.execute(
            """
            SELECT seq FROM conversation_events
            WHERE conversation_id=? AND status='completed' AND seq<=?
              AND json_valid(evidence_json)
              AND json_extract(evidence_json, '$.response_id')=?
            ORDER BY seq DESC LIMIT 1
            """,
            (conversation_id, at_or_before, response_id),
        ).fetchone()
    return int(row["seq"]) if row else None


def _stream_end(
    conversation_id: str,
    sequence: int,
    *,
    recovered: bool = False,
) -> str:
    payload = {
        "conversation_id": conversation_id,
        "sequence": sequence,
        "recovered": recovered,
    }
    return f"event: stream_end\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "conversation_id": item["conversation_id"],
        "sequence": int(item["seq"]),
        "status": item["status"],
        "agent": item["agent"],
        "content": item["content"],
        "summary": item["summary"],
        "time": item["created_at"],
        "evidence": item.get("evidence", {}),
        "hidden_chain_of_thought_included": False,
    }
