from __future__ import annotations

import json
import threading
from typing import Any

import httpx


class ScriptedProviderFactory:
    """HTTPX-compatible deterministic Provider test double.

    It exercises the real ProviderGateway/DLP/request lifecycle without making
    a network request. It is only wired by the evaluation Target Adapter.
    """

    def __init__(self, faults: list[str] | None = None):
        self.faults = set(faults or [])
        self._lock = threading.RLock()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **_: Any) -> "ScriptedProviderClient":
        return ScriptedProviderClient(self)

    def response(self, url: str, body: dict[str, Any]) -> httpx.Response:
        request = httpx.Request("POST", url, json=body)
        system = _system_prompt(body)
        with self._lock:
            self.calls.append({"url": url, "system_hash_input_length": len(system)})
        if "provider_timeout" in self.faults:
            raise httpx.ReadTimeout("scripted timeout", request=request)
        if "provider_auth_failed" in self.faults:
            return httpx.Response(401, json={"error": "scripted auth failure"}, request=request)
        if "provider_forbidden" in self.faults:
            return httpx.Response(403, json={"error": "scripted forbidden"}, request=request)
        if "provider_rate_limited" in self.faults:
            return httpx.Response(429, json={"error": "scripted rate limit"}, request=request)
        if "provider_empty_response" in self.faults:
            payload = _provider_payload(body, "")
            return httpx.Response(200, json=payload, request=request)
        if "provider_invalid_json" in self.faults:
            payload = _provider_payload(body, "not-json")
            return httpx.Response(200, json=payload, request=request)
        if "independent consumer-credit risk model reviewer" in system:
            reviewer_status = "pass"
            if "reviewer_block" in self.faults:
                reviewer_status = "block"
            elif "reviewer_revise" in self.faults:
                reviewer_status = "revise"
            content = json.dumps(
                {
                    "status": reviewer_status,
                    "issues": (
                        [
                            {
                                "code": "SCRIPTED_REVIEW_BLOCK",
                                "severity": "blocking",
                                "message": "评测注入的 Reviewer 阻断。",
                                "suggested_fix": "移除评测注入后重试。",
                            }
                        ]
                        if reviewer_status == "block"
                        else (
                            [
                                {
                                    "code": "SCRIPTED_REVIEW_REVISE",
                                    "severity": "warning",
                                    "message": "评测注入的 Reviewer 修改意见。",
                                    "suggested_fix": "修改后重新审核。",
                                }
                            ]
                            if reviewer_status == "revise"
                            else []
                        )
                    ),
                }
            )
        elif "risk-model planning Agent" in system:
            content = json.dumps(
                {"models": ["dummy", "scorecard", "regularized_logistic", "xgboost"]}
            )
        else:
            content = json.dumps({"status": "ok"})
        return httpx.Response(
            200,
            json=_provider_payload(body, content),
            headers={"x-request-id": f"fake-{len(self.calls):04d}"},
            request=request,
        )


class ScriptedProviderClient:
    def __init__(self, factory: ScriptedProviderFactory):
        self.factory = factory

    def __enter__(self) -> "ScriptedProviderClient":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> httpx.Response:
        del headers
        return self.factory.response(url, json)


def _system_prompt(body: dict[str, Any]) -> str:
    if isinstance(body.get("system"), str):
        return str(body["system"])
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "system":
            return str(message.get("content") or "")
    return ""


def _provider_payload(body: dict[str, Any], content: str) -> dict[str, Any]:
    if "system" in body:
        return {
            "content": [{"type": "text", "text": content}],
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }
