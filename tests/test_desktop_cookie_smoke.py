from __future__ import annotations

import sys

import pytest

from scripts import read_webview_cookie
from scripts import smoke_packaged_service


COOKIE_NAME = "risk_agent_desktop_session"
VALID_COOKIE = "a" * 64


def test_cookie_reader_accepts_only_explicit_ipv4_loopback_backend() -> None:
    assert (
        read_webview_cookie._normalise_backend_url("http://127.0.0.1:49152/")
        == "http://127.0.0.1:49152"
    )
    for invalid in (
        "http://localhost:49152",
        "http://127.0.0.1",
        "https://127.0.0.1:49152",
        "http://127.0.0.1:49152/api/v1/health",
        "http://127.0.0.1:49152/?next=https://example.com",
    ):
        with pytest.raises(ValueError):
            read_webview_cookie._normalise_backend_url(invalid)


def test_cookie_reader_selects_only_matching_loopback_page_target() -> None:
    backend_url = "http://127.0.0.1:49152"
    attacker = {
        "type": "page",
        "url": f"{backend_url}/",
        "webSocketDebuggerUrl": "ws://example.com:9222/devtools/page/attacker",
    }
    expected = "ws://127.0.0.1:9222/devtools/page/expected"
    target = {
        "type": "page",
        "url": f"{backend_url}/#/workbench?project=project_1",
        "webSocketDebuggerUrl": expected,
    }
    assert (
        read_webview_cookie._select_page_target(
            [attacker, target],
            backend_url=backend_url,
            debug_port=9222,
        )
        == expected
    )
    assert (
        read_webview_cookie._select_page_target(
            [{**target, "url": f"{backend_url}/api/v1/desktop/bootstrap?token=hidden"}],
            backend_url=backend_url,
            debug_port=9222,
        )
        is None
    )


@pytest.mark.parametrize(
    "page_url",
    [
        "http://127.0.0.1:49152",
        "http://127.0.0.1:49152/",
        "http://127.0.0.1:49152/#/workbench",
        "http://127.0.0.1:49152/#/workbench?project=project_1",
        "http://127.0.0.1:49152/#/workbench?project=project_1&run=run_1&mode=data",
        "http://127.0.0.1:49152/#/report?project=project_1&run=run_1",
        "http://127.0.0.1:49152/#/history?project=project_1",
    ],
)
def test_cookie_reader_accepts_only_application_root_and_known_hash_routes(
    page_url: str,
) -> None:
    assert read_webview_cookie._is_application_page_url(
        page_url,
        backend_url="http://127.0.0.1:49152",
    )


@pytest.mark.parametrize(
    "page_url",
    [
        "http://127.0.0.1:49153/#/workbench",
        "http://localhost:49152/#/workbench",
        "https://127.0.0.1:49152/#/workbench",
        "http://127.0.0.1:49152/api/v1/desktop/bootstrap?token=hidden#/workbench",
        "http://127.0.0.1:49152/?token=hidden#/workbench",
        "http://127.0.0.1:49152/?#/workbench",
        "http://127.0.0.1:49152/#/unknown",
        "http://127.0.0.1:49152/#/workbench?",
        "http://127.0.0.1:49152/#/workbench?project=invalid.value",
        "http://127.0.0.1:49152/#/workbench?project=project_1&project=project_2",
        "http://127.0.0.1:49152/#/report?mode=data",
        "http://127.0.0.1:49152/#/history?token=hidden",
    ],
)
def test_cookie_reader_rejects_non_application_page_targets(page_url: str) -> None:
    assert not read_webview_cookie._is_application_page_url(
        page_url,
        backend_url="http://127.0.0.1:49152",
    )


def test_cookie_reader_requires_expected_http_only_cookie_contract() -> None:
    valid = {
        "id": 1,
        "result": {
            "cookies": [
                {
                    "name": COOKIE_NAME,
                    "value": VALID_COOKIE,
                    "domain": "127.0.0.1",
                    "path": "/",
                    "httpOnly": True,
                }
            ]
        },
    }
    assert read_webview_cookie._extract_cookie(valid, cookie_name=COOKIE_NAME) == VALID_COOKIE

    for field, replacement in (
        ("value", "not-a-token"),
        ("domain", "example.com"),
        ("path", "/api"),
        ("httpOnly", False),
        ("name", "another_cookie"),
    ):
        invalid = {
            "id": 1,
            "result": {"cookies": [{**valid["result"]["cookies"][0], field: replacement}]},
        }
        assert read_webview_cookie._extract_cookie(invalid, cookie_name=COOKIE_NAME) is None


def test_cookie_reader_failure_is_generic_and_does_not_echo_internal_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_detail = "sensitive-cookie-like-detail"

    async def fail(**_: object) -> str:
        raise TimeoutError(sensitive_detail)

    monkeypatch.setattr(read_webview_cookie, "_read_cookie", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "read_webview_cookie.py",
            "--debug-port",
            "9222",
            "--backend-url",
            "http://127.0.0.1:49152",
        ],
    )
    assert read_webview_cookie.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "无法取得桌面浏览器会话" in captured.err
    assert sensitive_detail not in captured.err


def test_cookie_reader_reports_only_a_safe_probe_stage() -> None:
    error = read_webview_cookie.CookieProbeTimeout("debug_endpoint_unavailable")
    message = read_webview_cookie._probe_failure_message(error)
    assert message == "WebView2 调试端口未就绪，已阻止安装包冒烟。"
    assert "127.0.0.1" not in message

    unknown = read_webview_cookie.CookieProbeTimeout("sensitive-internal-detail")
    fallback = read_webview_cookie._probe_failure_message(unknown)
    assert fallback == "无法取得桌面浏览器会话，已阻止安装包冒烟。"
    assert unknown.stage not in fallback


def test_packaged_smoke_adds_valid_desktop_cookie_without_logging_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(smoke_packaged_service.DESKTOP_COOKIE_ENV, VALID_COOKIE)
    assert smoke_packaged_service._desktop_session_headers() == {
        "Cookie": f"{COOKIE_NAME}={VALID_COOKIE}"
    }

    invalid = "invalid-secret-value"
    monkeypatch.setenv(smoke_packaged_service.DESKTOP_COOKIE_ENV, invalid)
    with pytest.raises(RuntimeError) as exc_info:
        smoke_packaged_service._desktop_session_headers()
    assert invalid not in str(exc_info.value)
