from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.database import new_id, now_iso
from app.runtime import AppContext

from .dependencies import context


router = APIRouter(tags=["agent-conversation"])


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


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
    return ctx.conversations.send(project_id, payload.content)


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
            "conversation_events", {"conversation_id": conversation_id}, order_by="seq ASC", limit=5000
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
            raise HTTPException(400, "Last-Event-ID 必须是整数事件序号。")

    async def generate() -> AsyncIterator[str]:
        current = cursor
        quiet_ticks = 0
        while True:
            values = [
                item
                for item in ctx.database.list(
                    "conversation_events", {"conversation_id": conversation_id}, order_by="seq ASC", limit=5000
                )
                if int(item["seq"]) > current
            ]
            for item in values:
                payload = _event(item)
                current = payload["sequence"]
                yield f"id: {current}\nevent: conversation_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload["status"] == "completed" and (
                    response_id is None or payload["evidence"].get("response_id") == response_id
                ):
                    yield f"event: stream_end\ndata: {json.dumps({'conversation_id': conversation_id, 'sequence': current}, ensure_ascii=False)}\n\n"
                    return
                quiet_ticks = 0
            quiet_ticks += 1
            if quiet_ticks >= 30:
                yield f": keepalive {current}\n\n"
                quiet_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


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
