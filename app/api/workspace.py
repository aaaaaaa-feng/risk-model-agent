from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.workspace import WorkspaceManager, WorkspacePickerError, pick_workspace_directory
from app.bootstrap import AppContext

from .dependencies import context


router = APIRouter(tags=["workspace"])


class WorkspaceSelect(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


@router.get("/workspace")
def get_workspace(ctx: AppContext = Depends(context)) -> dict[str, Any]:
    manager = WorkspaceManager()
    projects = ctx.catalog.list_projects(include_archived=True)
    active_runs = [
        item
        for item in ctx.database.list("runs", limit=5000)
        if item.get("status") in {"queued", "running", "awaiting_decision"}
    ]
    return {
        "workspace": manager.status(
            ctx.paths,
            project_count=len(projects),
            active_run_count=len(active_runs),
        )
    }


@router.post("/workspace/native-picker")
async def native_picker() -> dict[str, Any]:
    # A desktop dialog may stay open until the bounded anti-hang timeout.
    # Keep it outside the ASGI event loop so health checks and the rest of the
    # local UI remain responsive while the user makes a selection.
    try:
        selected = await run_in_threadpool(pick_workspace_directory)
    except WorkspacePickerError as exc:
        status_code = 504 if exc.code == "WORKSPACE_NATIVE_PICKER_TIMEOUT" else 503
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc
    return {"path": selected, "cancelled": selected is None}


@router.post("/workspace/select")
def select_workspace(
    payload: WorkspaceSelect,
    request: Request,
    ctx: AppContext = Depends(context),
) -> dict[str, Any]:
    manager = WorkspaceManager()
    projects = ctx.catalog.list_projects(include_archived=True)
    active_runs = [
        item
        for item in ctx.database.list("runs", limit=5000)
        if item.get("status") in {"queued", "running", "awaiting_decision"}
    ]
    selected = manager.select(
        ctx.paths,
        payload.path,
        project_count=len(projects),
        active_run_count=len(active_runs),
    )
    if selected.root.resolve() != ctx.paths.root.resolve():
        # Build the new context before closing the old one.  If initialization
        # fails, the current app remains usable and the pointer can be retried
        # on the next request/startup without exposing a half-switched context.
        replacement = AppContext.create(selected)
        replacement.engine.recover_incomplete()
        ctx.shutdown()
        request.app.state.context = replacement
        active = replacement
    else:
        active = ctx
    projects = active.catalog.list_projects(include_archived=True)
    active_runs = [
        item
        for item in active.database.list("runs", limit=5000)
        if item.get("status") in {"queued", "running", "awaiting_decision"}
    ]
    return {
        "workspace": manager.status(
            active.paths,
            project_count=len(projects),
            active_run_count=len(active_runs),
        ),
        "switched": active is not ctx,
        "restart_required": False,
    }
