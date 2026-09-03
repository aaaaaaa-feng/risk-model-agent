"""Read one HttpOnly desktop session cookie from a CI-only WebView2 CDP port.

The production desktop client doesn't enable remote debugging. The Windows
installer smoke test opts in through ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS``
and uses this helper to prove that protected APIs work with the session created
by the real application WebView. Cookie values are the only successful stdout
output and must be captured by the caller rather than written to CI logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen


COOKIE_VALUE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_COOKIE_NAME = "risk_agent_desktop_session"


def _normalise_backend_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("后端地址必须是带明确端口的 127.0.0.1 HTTP 地址。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("后端地址的端口无效。") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("后端地址必须包含有效端口。")
    return f"http://127.0.0.1:{port}"


def _validate_debug_port(value: int) -> int:
    if not 1 <= value <= 65535:
        raise ValueError("WebView2 调试端口无效。")
    return value


def _select_page_target(
    targets: object,
    *,
    backend_url: str,
    debug_port: int,
) -> str | None:
    if not isinstance(targets, list):
        return None
    expected_pages = {backend_url, f"{backend_url}/"}
    for target in targets:
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        page_url = target.get("url")
        websocket_url = target.get("webSocketDebuggerUrl")
        if not isinstance(page_url, str) or not isinstance(websocket_url, str):
            continue
        # Do not attach while the one-use bootstrap URL is still loading. On a
        # successful exchange WebView2 first stores the new cookie and only
        # then follows the 303 redirect to the application root. This also
        # prevents a previous random-port session from winning a recovery race.
        if page_url not in expected_pages:
            continue
        parsed = urlsplit(websocket_url)
        try:
            websocket_port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme == "ws"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and websocket_port == debug_port
            and parsed.username is None
            and parsed.password is None
        ):
            return websocket_url
    return None


def _extract_cookie(
    response: object,
    *,
    cookie_name: str,
) -> str | None:
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    cookies = result.get("cookies")
    if not isinstance(cookies, list):
        return None
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        value = cookie.get("value")
        domain = cookie.get("domain")
        if (
            cookie.get("name") == cookie_name
            and isinstance(domain, str)
            and domain.lstrip(".") == "127.0.0.1"
            and cookie.get("path") == "/"
            and cookie.get("httpOnly") is True
            and isinstance(value, str)
            and COOKIE_VALUE_PATTERN.fullmatch(value)
        ):
            return value
    return None


def _read_targets(debug_port: int, timeout: float) -> object:
    endpoint = f"http://127.0.0.1:{debug_port}/json/list"
    with urlopen(endpoint, timeout=timeout) as response:  # noqa: S310 -- fixed loopback URL
        return json.loads(response.read().decode("utf-8"))


async def _read_cookie(
    *,
    debug_port: int,
    backend_url: str,
    cookie_name: str,
    timeout: float,
) -> str:
    # Imported lazily so ordinary source/unit checks don't require the CI-only
    # package extra. The Windows package workflow installs ``.[package]``.
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            targets = await asyncio.to_thread(_read_targets, debug_port, min(2.0, remaining))
            websocket_url = _select_page_target(
                targets,
                backend_url=backend_url,
                debug_port=debug_port,
            )
            if websocket_url is not None:
                async with connect(
                    websocket_url,
                    open_timeout=min(3.0, remaining),
                    close_timeout=1,
                    max_size=2**20,
                ) as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "id": 1,
                                "method": "Network.getCookies",
                                "params": {"urls": [f"{backend_url}/"]},
                            }
                        )
                    )
                    while time.monotonic() < deadline:
                        message = json.loads(
                            await asyncio.wait_for(
                                websocket.recv(),
                                timeout=min(3.0, max(0.1, deadline - time.monotonic())),
                            )
                        )
                        if isinstance(message, dict) and message.get("id") == 1:
                            cookie = _extract_cookie(message, cookie_name=cookie_name)
                            if cookie is not None:
                                return cookie
                            break
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, WebSocketException):
            # WebView2 and the bootstrap navigation start concurrently. Retry
            # only against the already validated loopback endpoint.
            pass
        await asyncio.sleep(0.25)
    raise TimeoutError("限定时间内未从目标 WebView 取得有效桌面会话。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", required=True, type=int)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--cookie-name", default=DEFAULT_COOKIE_NAME)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        debug_port = _validate_debug_port(args.debug_port)
        backend_url = _normalise_backend_url(args.backend_url)
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", args.cookie_name):
            raise ValueError("Cookie 名称无效。")
        if not 1 <= args.timeout <= 120:
            raise ValueError("等待时间必须介于 1 至 120 秒。")
        value = asyncio.run(
            _read_cookie(
                debug_port=debug_port,
                backend_url=backend_url,
                cookie_name=args.cookie_name,
                timeout=args.timeout,
            )
        )
    except (ImportError, OSError, RuntimeError, TimeoutError, ValueError):
        # Never include CDP payloads, target metadata, or cookie values in logs.
        print("无法取得桌面浏览器会话，已阻止安装包冒烟。", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
