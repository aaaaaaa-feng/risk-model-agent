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
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import router as api_router
from app.bootstrap import AppContext
from app.core.desktop_auth import DesktopAuth
from app.core.errors import (
    http_error_code,
    normalize_error_code,
    public_error_message,
    value_error_status,
)
from app.core.paths import AppPaths, get_paths, is_synced_path
from app.workers.model_adapters import available_models


APP_VERSION = "1.2.0"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
logger = logging.getLogger(__name__)


def create_app(
    paths: AppPaths | None = None,
    *,
    auto_migrate: bool | None = None,
) -> FastAPI:
    # 先捕获并清除桌面进程凭据，再构造任何可能在未来启动 Worker 的上下文。
    # 这样即使 AppContext 后续增加初始化副作用，也不会继承控制令牌。
    desktop_auth = DesktopAuth.capture_environment()
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
        if getattr(application.state, "desktop_shutdown_requested", False):
            logging.getLogger("uvicorn.error").info("desktop graceful shutdown completed")

    application = FastAPI(
        title="Risk Model Agent",
        version=APP_VERSION,
        description="Local-first binary consumer-credit risk modeling Agent workbench",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    application.state.context = context
    application.state.desktop_mode = desktop_auth.enabled
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
        desktop_session_error = desktop_auth.business_session_error(request)
        if desktop_session_error is not None:
            return desktop_session_error
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
        if desktop_auth.enabled:
            # This route is intentionally reachable by the Rust supervisor and
            # installer smoke before the WebView cookie exists. Keep it limited
            # to non-sensitive process identity; workspace and Provider details
            # remain behind the desktop session boundary.
            return desktop_auth.minimal_health(APP_VERSION)
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

    @application.get("/api/v1/desktop/ready", include_in_schema=False)
    def desktop_ready(request: Request) -> JSONResponse:
        """证明随机端口上的服务就是本次桌面客户端启动的后端。"""

        return desktop_auth.ready_response(request, APP_VERSION)

    @application.get("/api/v1/desktop/bootstrap", include_in_schema=False)
    def desktop_bootstrap(request: Request) -> Response:
        """Exchange the one-use WebView bootstrap capability for an HttpOnly cookie."""

        return desktop_auth.bootstrap_response(request)

    @application.post("/api/v1/desktop/shutdown", include_in_schema=False)
    def desktop_shutdown(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
        """仅允许本次桌面客户端请求后端完成有界优雅退出。"""

        auth_error = desktop_auth.shutdown_auth_error(request)
        if auth_error is not None:
            return auth_error
        callback = getattr(application.state, "desktop_shutdown_callback", None)
        if not callable(callback):
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "DESKTOP_SHUTDOWN_UNAVAILABLE",
                        "message": "本地服务暂时无法安全停止，请稍后重试。",
                    }
                },
            )
        application.state.desktop_shutdown_requested = True
        background_tasks.add_task(callback)
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "shutdown": "graceful"},
        )

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
    # 桌面冻结包不携带 httptools/uvloop。显式固定内置实现，避免升级安装残留的
    # 可选模块被 Uvicorn 自动探测为可用后，在真正解析请求时才失败。
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        http="h11",
        loop="asyncio",
        # The one-use desktop bootstrap capability is carried in the first URL.
        # Desktop access logs are therefore disabled fail-closed so query strings
        # never reach the backend log. Browser/development mode keeps its current
        # request logging behaviour.
        access_log=not bool(getattr(app.state, "desktop_mode", False)),
    )
    server = uvicorn.Server(config)

    def request_desktop_shutdown() -> None:
        server.should_exit = True

    app.state.desktop_shutdown_callback = request_desktop_shutdown
    app.state.desktop_shutdown_requested = False
    try:
        server.run()
    finally:
        app.state.desktop_shutdown_callback = None


if __name__ == "__main__":
    run()
