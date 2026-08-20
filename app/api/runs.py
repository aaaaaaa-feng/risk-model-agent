from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.runtime import AppContext

from .dependencies import context


router = APIRouter(tags=["runs-and-events"])


class RunCreate(BaseModel):
    project_id: str
    target_task_id: str
    mode: str | None = None


class DecisionResolve(BaseModel):
    approved: bool
    edits: dict[str, Any] = Field(default_factory=dict)


@router.post("/runs", status_code=202)
def create_run(payload: RunCreate, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    return {"run": ctx.engine.create_run(**payload.model_dump())}


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.get_project(project_id)
    return {
        "runs": [
            {key: value for key, value in item.items() if key != "state"}
            for item in ctx.database.list("runs", {"project_id": project_id}, limit=5000)
        ],
        "legacy_runs": [
            item
            for item in ctx.database.list("legacy_records", {"record_type": "run"}, limit=5000)
            if (item.get("metadata") or {}).get("v1_project_id") == project_id
        ],
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    run = ctx.catalog.require("runs", run_id)
    pending = [
        item
        for item in ctx.database.list("decisions", {"run_id": run_id}, limit=500)
        if item["status"] == "pending"
    ]
    return {"run": run, "pending_decisions": pending}


@router.get("/runs/{run_id}/decisions")
def list_decisions(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    return {"decisions": ctx.database.list("decisions", {"run_id": run_id}, limit=500)}


@router.post("/runs/{run_id}/decisions/{decision_id}", status_code=202)
def resolve_decision(
    run_id: str,
    decision_id: str,
    payload: DecisionResolve,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    return {
        "run": ctx.engine.resume(run_id, decision_id, payload.approved, payload.edits)
    }


@router.get("/runs/{run_id}/checkpoints")
def list_checkpoints(
    run_id: str,
    include_state: bool = False,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    values = ctx.database.list("checkpoints", {"run_id": run_id}, order_by="seq ASC", limit=5000)
    if not include_state:
        values = [
            {key: value for key, value in item.items() if key != "state"}
            for item in values
        ]
    return {"checkpoints": values}


@router.get("/runs/{run_id}/events")
def list_events(
    run_id: str,
    after: int = 0,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    events = [
        _event(item)
        for item in ctx.database.list("events", {"run_id": run_id}, order_by="seq ASC", limit=5000)
        if int(item["seq"]) > after
    ]
    return {"events": events, "next_sequence": events[-1]["sequence"] if events else after}


@router.get("/runs/{run_id}/events/stream")
async def stream_events(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ctx: AppContext = Depends(context),
) -> StreamingResponse:
    ctx.catalog.require("runs", run_id)
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
                for item in ctx.database.list("events", {"run_id": run_id}, order_by="seq ASC", limit=5000)
                if int(item["seq"]) > current
            ]
            for item in values:
                payload = _event(item)
                current = payload["sequence"]
                yield f"id: {current}\nevent: run_event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                quiet_ticks = 0
            run = ctx.catalog.require("runs", run_id)
            if run["status"] in {"succeeded", "failed", "blocked"} and current >= int(run["seq"]):
                yield f"event: stream_end\ndata: {json.dumps({'run_id': run_id, 'status': run['status'], 'sequence': current}, ensure_ascii=False)}\n\n"
                return
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


@router.get("/runs/{run_id}/provider-requests")
def provider_requests(run_id: str, ctx: AppContext = Depends(context)) -> dict[str, Any]:
    ctx.catalog.require("runs", run_id)
    return {
        "requests": ctx.database.list(
            "provider_requests", {"run_id": run_id}, order_by="created_at ASC", limit=5000
        )
    }


def _event(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "run_id": item["run_id"],
        "sequence": int(item["seq"]),
        "stage": item["stage"],
        "node": item["node"],
        "agent": item["agent"],
        "tool": item.get("tool"),
        "status": item["status"],
        "summary": item["summary"],
        "time": item["created_at"],
        "evidence": item.get("evidence", {}),
        "hidden_chain_of_thought_included": False,
    }
