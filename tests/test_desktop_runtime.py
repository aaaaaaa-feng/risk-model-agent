from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.core.desktop_auth import (
    DESKTOP_BOOTSTRAP_TOKEN_ENV,
    DESKTOP_SESSION_COOKIE,
    DESKTOP_TOKEN_ENV,
)
from app.main import APP_VERSION, create_app


DESKTOP_TOKEN = "ab" * 32
BOOTSTRAP_TOKEN = "cd" * 32


def test_desktop_ready_endpoint_is_hidden_outside_desktop_runtime(app_paths, monkeypatch):
    monkeypatch.delenv(DESKTOP_TOKEN_ENV, raising=False)
    monkeypatch.delenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, raising=False)
    application = create_app(app_paths, auto_migrate=False)

    with TestClient(application) as client:
        response = client.get("/api/v1/desktop/ready")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DESKTOP_RUNTIME_DISABLED"


def test_desktop_ready_endpoint_requires_the_current_launch_token(app_paths, monkeypatch):
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, DESKTOP_TOKEN)
    monkeypatch.setenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, BOOTSTRAP_TOKEN)
    application = create_app(app_paths, auto_migrate=False)

    with TestClient(application) as client:
        missing = client.get("/api/v1/desktop/ready")
        wrong = client.get(
            "/api/v1/desktop/ready",
            headers={"x-risk-agent-desktop-token": "ef" * 32},
        )
        non_ascii = client.get(
            "/api/v1/desktop/ready",
            headers=[(b"x-risk-agent-desktop-token", b"\xff")],
        )
        ready = client.get(
            "/api/v1/desktop/ready",
            headers={"x-risk-agent-desktop-token": DESKTOP_TOKEN},
        )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert non_ascii.status_code == 403
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "runtime": "local",
        "desktop": True,
        "version": APP_VERSION,
    }


def test_desktop_ready_fails_closed_when_bootstrap_capability_is_missing(app_paths, monkeypatch):
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, DESKTOP_TOKEN)
    monkeypatch.delenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, raising=False)
    application = create_app(app_paths, auto_migrate=False)

    with TestClient(application) as client:
        ready = client.get(
            "/api/v1/desktop/ready",
            headers={"x-risk-agent-desktop-token": DESKTOP_TOKEN},
        )
        bootstrap = client.get(
            "/api/v1/desktop/bootstrap",
            params={"token": BOOTSTRAP_TOKEN},
            follow_redirects=False,
        )

    assert ready.status_code == 503
    assert ready.json() == {
        "error": {
            "code": "DESKTOP_BOOTSTRAP_UNAVAILABLE",
            "message": "桌面客户端会话初始化凭据不可用，请返回启动页重试。",
        }
    }
    assert bootstrap.status_code == 403
    assert BOOTSTRAP_TOKEN not in bootstrap.text


def test_desktop_shutdown_is_hidden_without_desktop_runtime(app_paths, monkeypatch):
    monkeypatch.delenv(DESKTOP_TOKEN_ENV, raising=False)
    monkeypatch.delenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, raising=False)
    application = create_app(app_paths, auto_migrate=False)

    with TestClient(application) as client:
        response = client.post("/api/v1/desktop/shutdown")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DESKTOP_RUNTIME_DISABLED"


def test_desktop_shutdown_requires_token_and_runs_callback(app_paths, monkeypatch):
    called: list[str] = []
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, DESKTOP_TOKEN)
    monkeypatch.setenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, BOOTSTRAP_TOKEN)
    application = create_app(app_paths, auto_migrate=False)
    application.state.desktop_shutdown_callback = lambda: called.append("shutdown")

    with TestClient(application) as client:
        wrong = client.post(
            "/api/v1/desktop/shutdown",
            headers={"x-risk-agent-desktop-token": "ef" * 32},
        )
        non_ascii = client.post(
            "/api/v1/desktop/shutdown",
            headers=[(b"x-risk-agent-desktop-token", b"\xff")],
        )
        accepted = client.post(
            "/api/v1/desktop/shutdown",
            headers={"x-risk-agent-desktop-token": DESKTOP_TOKEN},
        )

    assert wrong.status_code == 403
    assert non_ascii.status_code == 403
    assert called == ["shutdown"]
    assert accepted.status_code == 202
    assert accepted.json() == {"status": "accepted", "shutdown": "graceful"}


def test_invalid_desktop_token_keeps_browser_mode_compatible(app_paths, monkeypatch):
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, "not-a-32-byte-hex-token")
    monkeypatch.setenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, BOOTSTRAP_TOKEN)
    application = create_app(app_paths, auto_migrate=False)

    assert DESKTOP_TOKEN_ENV not in os.environ
    assert DESKTOP_BOOTSTRAP_TOKEN_ENV not in os.environ
    with TestClient(application) as client:
        session = client.get("/api/v1/session")
        health = client.get("/api/v1/health")
        ready = client.get(
            "/api/v1/desktop/ready",
            headers={"x-risk-agent-desktop-token": "not-a-32-byte-hex-token"},
        )

    assert session.status_code == 200
    assert session.json()["request_token"]
    assert "data_directory" in health.json()
    assert "desktop" not in health.json()
    assert ready.status_code == 404


def test_desktop_bootstrap_creates_one_use_httponly_session(app_paths, monkeypatch):
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, DESKTOP_TOKEN)
    monkeypatch.setenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, BOOTSTRAP_TOKEN)
    application = create_app(app_paths, auto_migrate=False)

    assert DESKTOP_TOKEN_ENV not in os.environ
    assert DESKTOP_BOOTSTRAP_TOKEN_ENV not in os.environ
    with TestClient(application) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "version": APP_VERSION,
            "runtime": "local",
            "desktop": True,
        }
        for protected_path in (
            "/",
            "/assets/not-present.js",
            "/api/v1/session",
            "/api/v1/capabilities",
            "/api/v1/runs/not-present/events",
            "/api/v1/reports/not-present/download",
        ):
            denied = client.get(protected_path)
            assert denied.status_code == 401, protected_path
            assert denied.json()["error"]["code"] == "DESKTOP_SESSION_REQUIRED"

        missing = client.get(
            "/api/v1/desktop/bootstrap",
            follow_redirects=False,
        )
        wrong = client.get(
            "/api/v1/desktop/bootstrap",
            params={"token": "ef" * 32},
            follow_redirects=False,
        )
        non_ascii = client.get(
            "/api/v1/desktop/bootstrap",
            params={"token": "凭据"},
            follow_redirects=False,
        )
        duplicate = client.get(
            f"/api/v1/desktop/bootstrap?token={BOOTSTRAP_TOKEN}&token={BOOTSTRAP_TOKEN}",
            follow_redirects=False,
        )
        for rejected in (missing, wrong, non_ascii, duplicate):
            assert rejected.status_code == 403
            assert rejected.json() == {
                "error": {
                    "code": "DESKTOP_BOOTSTRAP_INVALID",
                    "message": "桌面客户端会话初始化未通过，请返回启动页重试。",
                }
            }
            assert BOOTSTRAP_TOKEN not in rejected.text

        established = client.get(
            "/api/v1/desktop/bootstrap",
            params={"token": BOOTSTRAP_TOKEN},
            follow_redirects=False,
        )
        assert established.status_code == 303
        assert established.headers["location"] == "/"
        set_cookie = established.headers["set-cookie"]
        assert set_cookie.startswith(f"{DESKTOP_SESSION_COOKIE}=")
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "Path=/" in set_cookie
        assert BOOTSTRAP_TOKEN not in established.text
        assert BOOTSTRAP_TOKEN not in "\n".join(
            f"{key}: {value}" for key, value in established.headers.items()
        )

        session = client.get("/api/v1/session")
        assert session.status_code == 200
        assert session.json()["request_token"]
        assert client.get("/api/v1/capabilities").status_code == 200

        replay = client.get(
            "/api/v1/desktop/bootstrap",
            params={"token": BOOTSTRAP_TOKEN},
            follow_redirects=False,
        )
        assert replay.status_code == 403
        assert replay.json()["error"]["code"] == "DESKTOP_BOOTSTRAP_INVALID"

        client.cookies.delete(DESKTOP_SESSION_COOKIE)
        denied_again = client.get("/api/v1/session")
        assert denied_again.status_code == 401


def test_local_browser_session_cannot_replace_desktop_cookie(app_paths, monkeypatch):
    monkeypatch.setenv(DESKTOP_TOKEN_ENV, DESKTOP_TOKEN)
    monkeypatch.setenv(DESKTOP_BOOTSTRAP_TOKEN_ENV, BOOTSTRAP_TOKEN)
    application = create_app(app_paths, auto_migrate=False)

    with TestClient(application) as client:
        non_ascii_cookie = client.get(
            "/api/v1/session",
            headers=[(b"cookie", b"risk_agent_desktop_session=\xff")],
        )
        client.cookies.set("risk_agent_session", "attacker-local-session")
        response = client.post(
            "/api/v1/projects",
            headers={"x-risk-agent-session": "attacker-local-session"},
            json={"name": "must-not-be-created"},
        )

    assert non_ascii_cookie.status_code == 401
    assert non_ascii_cookie.json()["error"]["code"] == "DESKTOP_SESSION_REQUIRED"
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "DESKTOP_SESSION_REQUIRED"
