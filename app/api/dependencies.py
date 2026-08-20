from __future__ import annotations

from fastapi import Request

from app.runtime import AppContext


def context(request: Request) -> AppContext:
    return request.app.state.context
