"""HTTP authentication boundary for the embedded desktop WebView.

The Rust supervisor gives the backend two independent process capabilities:
one identifies lifecycle control requests and one can be exchanged exactly once
for an HttpOnly browser session.  This module owns their validation, lifetime,
cookie contract and stable public errors so the application entry point only
assembles routes and middleware.
"""

from __future__ import annotations

import os
import secrets
import threading
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response


DESKTOP_TOKEN_ENV = "RISK_AGENT_DESKTOP_TOKEN"
DESKTOP_BOOTSTRAP_TOKEN_ENV = "RISK_AGENT_DESKTOP_BOOTSTRAP_TOKEN"
DESKTOP_SESSION_COOKIE = "risk_agent_desktop_session"
DESKTOP_CONTROL_HEADER = "x-risk-agent-desktop-token"
DESKTOP_PUBLIC_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/desktop/ready",
        "/api/v1/desktop/bootstrap",
        "/api/v1/desktop/shutdown",
    }
)


def _valid_secret(value: str) -> str | None:
    """Accept only the 32-byte lowercase hexadecimal contract emitted by Rust."""

    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


class DesktopAuth:
    """Process-local desktop capabilities and their one-use WebView exchange."""

    def __init__(self, launch_token: str | None, bootstrap_token: str | None) -> None:
        self._launch_token = _valid_secret(launch_token or "")
        self._bootstrap_token = _valid_secret(bootstrap_token or "") if self._launch_token else None
        self._bootstrap_configured = self._bootstrap_token is not None
        self._session_token = secrets.token_hex(32) if self._launch_token else None
        self._bootstrap_lock = threading.Lock()

    @classmethod
    def capture_environment(cls) -> DesktopAuth:
        """Capture then erase capabilities before any local Worker is launched."""

        supplied_launch_token = os.environ.pop(DESKTOP_TOKEN_ENV, "")
        supplied_bootstrap_token = os.environ.pop(DESKTOP_BOOTSTRAP_TOKEN_ENV, "")
        launch_token = _valid_secret(supplied_launch_token)
        if supplied_launch_token and launch_token is None:
            raise RuntimeError("DESKTOP_STARTUP_TOKEN_INVALID")
        if supplied_bootstrap_token and _valid_secret(supplied_bootstrap_token) is None:
            raise RuntimeError("DESKTOP_BOOTSTRAP_TOKEN_INVALID")
        if supplied_bootstrap_token and launch_token is None:
            raise RuntimeError("DESKTOP_BOOTSTRAP_WITHOUT_STARTUP_TOKEN")
        bootstrap_token = _valid_secret(supplied_bootstrap_token) if launch_token else None
        return cls(launch_token, bootstrap_token)

    @property
    def enabled(self) -> bool:
        return self._launch_token is not None

    def minimal_health(self, version: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": version,
            "runtime": "local",
            "desktop": True,
        }

    def business_session_error(self, request: Request) -> JSONResponse | None:
        if not self.enabled or request.url.path in DESKTOP_PUBLIC_PATHS:
            return None
        supplied = _valid_secret(request.cookies.get(DESKTOP_SESSION_COOKIE, ""))
        if (
            supplied
            and self._session_token
            and secrets.compare_digest(supplied, self._session_token)
        ):
            return None
        return _error(
            401,
            "DESKTOP_SESSION_REQUIRED",
            "请从桌面客户端重新进入本地应用。",
        )

    def ready_response(self, request: Request, version: str) -> JSONResponse:
        auth_error = self._control_error(request, "桌面客户端启动校验未通过。")
        if auth_error is not None:
            return auth_error
        if not self._bootstrap_configured:
            return _error(
                503,
                "DESKTOP_BOOTSTRAP_UNAVAILABLE",
                "桌面客户端会话初始化凭据不可用，请返回启动页重试。",
            )
        return JSONResponse(self.minimal_health(version))

    def bootstrap_response(self, request: Request) -> Response:
        if not self.enabled:
            return self._disabled_error()

        query_tokens = request.query_params.getlist("token")
        supplied = _valid_secret(query_tokens[0]) if len(query_tokens) == 1 else None
        with self._bootstrap_lock:
            expected = self._bootstrap_token
            if not supplied or not expected or not secrets.compare_digest(supplied, expected):
                return _error(
                    403,
                    "DESKTOP_BOOTSTRAP_INVALID",
                    "桌面客户端会话初始化未通过，请返回启动页重试。",
                )
            self._bootstrap_token = None

        session_token = self._session_token
        if session_token is None:  # Defensive fail-closed guard for constructor invariants.
            return _error(
                503,
                "DESKTOP_BOOTSTRAP_UNAVAILABLE",
                "桌面客户端会话初始化凭据不可用，请返回启动页重试。",
            )
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            DESKTOP_SESSION_COOKIE,
            session_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    def shutdown_auth_error(self, request: Request) -> JSONResponse | None:
        return self._control_error(request, "桌面客户端停止校验未通过。")

    def _control_error(self, request: Request, invalid_message: str) -> JSONResponse | None:
        if not self.enabled:
            return self._disabled_error()
        supplied = _valid_secret(request.headers.get(DESKTOP_CONTROL_HEADER, ""))
        if supplied and self._launch_token and secrets.compare_digest(supplied, self._launch_token):
            return None
        return _error(403, "DESKTOP_STARTUP_TOKEN_INVALID", invalid_message)

    @staticmethod
    def _disabled_error() -> JSONResponse:
        return _error(
            404,
            "DESKTOP_RUNTIME_DISABLED",
            "当前服务不是由桌面客户端启动。",
        )
