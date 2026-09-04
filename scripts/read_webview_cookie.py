"""Read one HttpOnly desktop session cookie from a CI-only WebView2 CDP port.

The production desktop client doesn't enable remote debugging. The Windows
installer smoke test opts in through a strict CI-only port request that the
Rust shell applies with WebView2's programmatic browser-arguments API. This
helper proves that protected APIs work with the session created by the real
application WebView. Cookie values are the only successful stdout output and
must be captured by the caller rather than written to CI logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from urllib.parse import parse_qsl, urlsplit
from urllib.request import urlopen


COOKIE_VALUE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ROUTE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,159}$")
ALLOWED_ROUTE_VIEWS = {"workbench", "report", "history"}
DEFAULT_COOKIE_NAME = "risk_agent_desktop_session"


class CookieProbeTimeout(TimeoutError):
    """Bounded, non-sensitive stage result for Windows CI diagnostics."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


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


def _is_application_page_url(value: str, *, backend_url: str) -> bool:
    page = urlsplit(value)
    expected = urlsplit(backend_url)
    # ``urlsplit`` normalises a bare ``?`` to an empty query. Reject the
    # delimiter itself before parsing so only fragment-local route parameters
    # are accepted.
    if "?" in value.partition("#")[0]:
        return False
    try:
        page_port = page.port
        expected_port = expected.port
    except ValueError:
        return False
    if (
        page.scheme != expected.scheme
        or page.hostname != expected.hostname
        or page_port != expected_port
        or page.username is not None
        or page.password is not None
        or page.path not in {"", "/"}
        or page.query
    ):
        return False
    if not page.fragment:
        return True

    route, separator, raw_parameters = page.fragment.partition("?")
    view = route.removeprefix("/") if route.startswith("/") else ""
    if view not in ALLOWED_ROUTE_VIEWS:
        return False
    if not separator:
        return True
    if not raw_parameters:
        return False
    try:
        parameters = parse_qsl(
            raw_parameters,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=3,
        )
    except ValueError:
        return False
    seen: set[str] = set()
    for key, parameter_value in parameters:
        if key in seen or key not in {"project", "run", "mode"}:
            return False
        seen.add(key)
        if key in {"project", "run"} and not ROUTE_IDENTIFIER_PATTERN.fullmatch(parameter_value):
            return False
        if key == "mode" and (view != "workbench" or parameter_value != "data"):
            return False
    return True


def _select_page_target(
    targets: object,
    *,
    backend_url: str,
    debug_port: int,
) -> str | None:
    if not isinstance(targets, list):
        return None
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
        if not _is_application_page_url(page_url, backend_url=backend_url):
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
    saw_debug_endpoint = False
    saw_matching_page = False
    saw_cookie_response = False
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            targets = await asyncio.to_thread(_read_targets, debug_port, min(2.0, remaining))
            saw_debug_endpoint = True
            websocket_url = _select_page_target(
                targets,
                backend_url=backend_url,
                debug_port=debug_port,
            )
            if websocket_url is not None:
                saw_matching_page = True
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
                            saw_cookie_response = True
                            cookie = _extract_cookie(message, cookie_name=cookie_name)
                            if cookie is not None:
                                return cookie
                            break
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, WebSocketException):
            # WebView2 and the bootstrap navigation start concurrently. Retry
            # only against the already validated loopback endpoint.
            pass
        await asyncio.sleep(0.25)
    if not saw_debug_endpoint:
        raise CookieProbeTimeout("debug_endpoint_unavailable")
    if not saw_matching_page:
        raise CookieProbeTimeout("application_page_not_found")
    if not saw_cookie_response:
        raise CookieProbeTimeout("cookie_command_unavailable")
    raise CookieProbeTimeout("valid_cookie_not_found")


def _probe_failure_message(error: BaseException) -> str:
    if isinstance(error, CookieProbeTimeout):
        return {
            "debug_endpoint_unavailable": "WebView2 调试端口未就绪，已阻止安装包冒烟。",
            "application_page_not_found": "WebView2 已启动，但未找到目标应用页面，已阻止安装包冒烟。",
            "cookie_command_unavailable": "目标应用页面已找到，但会话读取命令未完成，已阻止安装包冒烟。",
            "valid_cookie_not_found": "目标应用页面未返回有效桌面会话，已阻止安装包冒烟。",
        }.get(error.stage, "无法取得桌面浏览器会话，已阻止安装包冒烟。")
    return "无法取得桌面浏览器会话，已阻止安装包冒烟。"


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
    except (ImportError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        # Never include CDP payloads, target metadata, or cookie values in logs.
        print(_probe_failure_message(error), file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
