from __future__ import annotations

from fastapi import Request

from app.bootstrap import AppContext


def context(request: Request) -> AppContext:
    return request.app.state.context
