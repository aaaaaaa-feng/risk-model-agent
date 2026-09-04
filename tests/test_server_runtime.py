from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

import uvicorn

import app.main as main_module


def test_server_uses_h11_when_upgrade_leaves_partial_httptools(monkeypatch):
    """旧安装残留可骗过 auto 探测，但正式启动必须绕开该可选模块。"""

    stale_httptools = ModuleType("httptools")
    stale_httptools.__path__ = ["旧安装残留/httptools"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httptools", stale_httptools)
    protocol_modules = (
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.httptools_impl",
    )
    previous_modules = {name: sys.modules.get(name) for name in protocol_modules}
    try:
        for name in protocol_modules:
            sys.modules.pop(name, None)
        auto_module = importlib.import_module("uvicorn.protocols.http.auto")
        assert auto_module.AutoHTTPProtocol.__module__.endswith("httptools_impl")
        assert not hasattr(stale_httptools, "HttpRequestParser")
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    selected: dict[str, Any] = {}

    def inspect_run(server: uvicorn.Server) -> None:
        config = server.config
        config.log_config = None
        config.load()
        selected.update(
            {
                "http": config.http,
                "loop": config.loop,
                "protocol": config.http_protocol_class,
                "access_log": config.access_log,
            }
        )

    monkeypatch.setenv("RISK_AGENT_OPEN_BROWSER", "0")
    monkeypatch.delenv("RISK_AGENT_DESKTOP_TOKEN", raising=False)
    monkeypatch.setattr(main_module.app.state, "desktop_mode", False)
    monkeypatch.setattr(main_module.uvicorn.Server, "run", inspect_run)

    main_module.run()

    assert selected["http"] == "h11"
    assert selected["loop"] == "asyncio"
    assert selected["protocol"].__module__ == "uvicorn.protocols.http.h11_impl"
    assert selected["access_log"] is True


def test_desktop_server_disables_access_log_for_bootstrap_query(monkeypatch):
    selected: dict[str, Any] = {}

    def inspect_run(server: uvicorn.Server) -> None:
        selected["access_log"] = server.config.access_log

    monkeypatch.setenv("RISK_AGENT_OPEN_BROWSER", "0")
    monkeypatch.setattr(main_module.app.state, "desktop_mode", True)
    monkeypatch.setattr(main_module.uvicorn.Server, "run", inspect_run)

    main_module.run()

    assert selected["access_log"] is False
