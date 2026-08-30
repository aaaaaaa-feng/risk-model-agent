from __future__ import annotations

import ipaddress
import logging
import multiprocessing
import os
import platform
import secrets
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import router as api_router
from app.core.errors import (
    http_error_code,
    normalize_error_code,
    public_error_message,
    value_error_status,
)
from app.core.paths import AppPaths, get_paths, is_synced_path
from app.bootstrap import AppContext
from app.workers.model_adapters import available_models


APP_VERSION = "1.0.2"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
logger = logging.getLogger(__name__)


def create_app(
    paths: AppPaths | None = None,
    *,
    auto_migrate: bool | None = None,
) -> FastAPI:
    context = AppContext.create(paths or get_paths())
    local_session_token = secrets.token_urlsafe(32)
    should_migrate = (
        auto_migrate
        if auto_migrate is not None
        else os.getenv("RISK_AGENT_AUTO_MIGRATE", "1").strip() not in {"0", "false", "False"}
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_context = application.state.context
        if should_migrate:
            source = Path(os.getenv("RISK_AGENT_LEGACY_RUNTIME", str(Path.cwd() / "runtime")))
            if source.resolve() != active_context.paths.root.resolve():
                active_context.migration.migrate(source)
        active_context.engine.recover_incomplete()
        yield
        application.state.context.shutdown()

    application = FastAPI(
        title="Risk Model Agent",
        version=APP_VERSION,
        description="Local-first binary consumer-credit risk modeling Agent workbench",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    application.state.context = context
    application.include_router(api_router, prefix="/api/v1")

    @application.exception_handler(KeyError)
    async def not_found(_: Request, __: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "RESOURCE_NOT_FOUND",
                    "message": public_error_message("RESOURCE_NOT_FOUND", 404),
                }
            },
        )

    @application.exception_handler(ValueError)
    async def invalid(_: Request, error: ValueError) -> JSONResponse:
        code = normalize_error_code(error)
        status = value_error_status(code)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": public_error_message(code, status)}},
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation(_: Request, __: RequestValidationError) -> JSONResponse:
        code = "REQUEST_VALIDATION_FAILED"
        return JSONResponse(
            status_code=422,
            content={"error": {"code": code, "message": public_error_message(code, 422)}},
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
        fallback = http_error_code(error.status_code)
        detail = error.detail if isinstance(error.detail, dict) else {}
        code = normalize_error_code(detail.get("code"), fallback)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": code,
                    "message": public_error_message(code, error.status_code),
                }
            },
        )

    @application.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        # Do not copy exception text, request bodies, URLs, filesystem paths or
        # tracebacks into either the response or this ordinary log.
        logger.error(
            "未处理的 API 异常 method=%s type=%s",
            request.method,
            type(error).__name__,
        )
        code = "INTERNAL_SERVER_ERROR"
        return JSONResponse(
            status_code=500,
            content={"error": {"code": code, "message": public_error_message(code, 500)}},
        )

    @application.middleware("http")
    async def local_security_headers(request: Request, call_next: Any) -> Any:
        if not _allowed_host_header(request.headers.get("host", "")):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {"code": "LOCAL_HOST_REQUIRED", "message": "仅允许通过本机地址访问。"}
                },
            )
        origin = request.headers.get("origin")
        if origin and not _allowed_origin(origin):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "CROSS_ORIGIN_REQUEST_FORBIDDEN",
                        "message": "已拒绝跨站请求。",
                    }
                },
            )
        if (
            request.method in MUTATING_METHODS
            and request.headers.get("sec-fetch-site", "").lower() == "cross-site"
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "CROSS_SITE_MUTATION_FORBIDDEN",
                        "message": "已拒绝跨站写入。",
                    }
                },
            )
        browser_request = bool(origin or request.headers.get("sec-fetch-site"))
        if (
            request.method in MUTATING_METHODS
            and browser_request
            and (
                request.cookies.get("risk_agent_session") != local_session_token
                or request.headers.get("x-risk-agent-session") != local_session_token
            )
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "LOCAL_SESSION_REQUIRED",
                        "message": "请从本机应用页面重新进入。",
                    }
                },
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        return response

    @application.get("/api/v1/health", tags=["system"])
    def health() -> dict[str, Any]:
        active_context = application.state.context
        settings = active_context.pipeline._gateway("").settings
        return {
            "status": "ok",
            "version": APP_VERSION,
            "runtime": "local",
            "python": platform.python_version(),
            "platform": platform.system(),
            "data_directory": str(active_context.paths.root),
            "synced_path_warning": is_synced_path(active_context.paths.root),
            "langgraph_checkpoint": active_context.engine.persistence_mode,
            "provider": {
                "enabled": settings.llm_enabled,
                "name": settings.provider,
                "model": settings.model,
            },
            "models": available_models(),
            "mcp": {"enabled": False, "boundary": "typed_tool_registry_only"},
            "raw_data_cloud_upload": False,
        }

    @application.get("/api/v1/session", include_in_schema=False)
    def local_session() -> JSONResponse:
        response = JSONResponse(
            {
                "status": "ok",
                "scope": "local-browser-session",
                "request_token": local_session_token,
            }
        )
        response.set_cookie(
            "risk_agent_session",
            local_session_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    frontend = _frontend_dir()
    if (frontend / "assets").exists():
        application.mount(
            "/assets", StaticFiles(directory=frontend / "assets"), name="frontend-assets"
        )

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Any:
        if full_path.startswith("api/"):
            raise HTTPException(404, detail={"code": "ROUTE_NOT_FOUND"})
        index = frontend / "index.html"
        if index.exists():
            response = FileResponse(index)
            response.set_cookie(
                "risk_agent_session",
                local_session_token,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
            return response
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "FRONTEND_NOT_BUILT",
                    "message": "请先执行前端构建；API 已在 /api/v1/health 可用。",
                }
            },
        )

    return application


def _frontend_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "frontend_dist"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _loopback_name(value: str) -> bool:
    lowered = value.strip().lower().rstrip(".")
    if lowered in {"localhost", "testserver"}:
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _allowed_host_header(value: str) -> bool:
    try:
        hostname = urlsplit(f"//{value}").hostname
    except ValueError:
        return False
    return bool(hostname and _loopback_name(hostname))


def _allowed_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(
        parsed.hostname and _loopback_name(parsed.hostname)
    )


def validate_bind_host(host: str) -> str:
    if host.strip().lower().rstrip(".") == "testserver" or not _loopback_name(host):
        raise ValueError("REMOTE_BIND_DISABLED: V1 仅支持 127.0.0.1、localhost 或 ::1")
    return host


app = create_app()


def run() -> None:
    multiprocessing.freeze_support()
    host = validate_bind_host(os.getenv("RISK_AGENT_HOST", "127.0.0.1"))
    port = int(os.getenv("RISK_AGENT_PORT", "8765"))
    if os.getenv("RISK_AGENT_OPEN_BROWSER", "1") not in {"0", "false", "False"}:
        import threading

        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
