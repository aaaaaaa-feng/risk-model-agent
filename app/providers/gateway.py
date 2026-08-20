from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.core.config import Settings, SettingsStore
from app.core.paths import AppPaths
from app.core.security import sha256_bytes, validate_safe_evidence

from .secrets import SecretStore


@dataclass
class ProviderResult:
    ok: bool
    content: str = ""
    error_code: str | None = None
    error_message: str | None = None
    model: str = ""
    usage: dict[str, Any] | None = None
    payload_hash: str = ""


def _parse_json_content(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("PROVIDER_SCHEMA_INVALID")
    return value


class ProviderGateway:
    """The only network egress path available to the Agent layer."""

    def __init__(
        self,
        settings: Settings | None = None,
        api_key: str | None = None,
        client_factory: Any = None,
        budget_guard: Callable[[int], str | None] | None = None,
        usage_callback: Callable[[int, str], None] | None = None,
        request_callback: Callable[[str, dict[str, Any], str], None] | None = None,
        paths: AppPaths | None = None,
    ):
        self.settings = settings or SettingsStore().load()
        self._api_key_override = (api_key or "").strip()
        self._client_factory = client_factory or httpx.Client
        self._budget_guard = budget_guard
        self._usage_callback = usage_callback
        self._request_callback = request_callback
        self._secrets = SecretStore(paths)

    @property
    def key(self) -> str:
        return self._api_key_override or self._secrets.read()

    @property
    def configured(self) -> bool:
        return bool(self.key and self.settings.base_url and self.settings.model)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_enabled and self.configured)

    @property
    def api_format(self) -> str:
        return "anthropic" if self.settings.api_format == "anthropic" else "openai"

    def endpoint(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if self.api_format == "openai":
            return base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        if base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def status(self) -> dict[str, Any]:
        active = self.enabled
        return {
            "configured": self.configured,
            "enabled": active,
            "provider": self.settings.provider,
            "api_format": self.api_format,
            "endpoint": self.endpoint() if self.settings.base_url else "",
            "model": self.settings.model,
            "reviewer_model": self.settings.reviewer_model or self.settings.model,
            "mode": "external-enabled" if active else "deterministic-fallback",
            "message": (
                "外部 API 已启用；仅允许 SafeEvidence 出站。"
                if active
                else "外部 API 未启用，使用本地确定性降级。"
            ),
        }

    def _body(
        self, system_prompt: str, evidence: dict[str, Any], model: str, max_tokens: int
    ) -> dict[str, Any]:
        user_content = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        if self.api_format == "anthropic":
            return {
                "model": model,
                "system": system_prompt,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [{"role": "user", "content": user_content}],
            }
        body = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if self.settings.provider == "openai" or model.startswith(("gpt-5", "o3", "o4")):
            body.pop("temperature", None)
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        return body

    def _headers(self) -> dict[str, str]:
        if self.api_format == "anthropic":
            return {
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {"authorization": f"Bearer {self.key}", "content-type": "application/json"}

    def _response(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if self.api_format == "anthropic":
            blocks = payload.get("content") or []
            content = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            usage = dict(payload.get("usage") or {})
            usage["total_tokens"] = int(usage.get("input_tokens") or 0) + int(
                usage.get("output_tokens") or 0
            )
        else:
            choices = payload.get("choices") or []
            message = choices[0].get("message") if choices else None
            content = str((message or {}).get("content") or "").strip()
            usage = dict(payload.get("usage") or {})
        if not content:
            raise ValueError("PROVIDER_EMPTY_RESPONSE")
        return content, usage

    def complete(
        self,
        system_prompt: str,
        evidence: dict[str, Any],
        model: str | None = None,
        max_tokens: int = 2048,
        purpose: str = "agent",
        allow_disabled_for_test: bool = False,
    ) -> ProviderResult:
        if not self.configured or (not self.settings.llm_enabled and not allow_disabled_for_test):
            return ProviderResult(False, error_code="PROVIDER_DISABLED", error_message="Provider 未启用或配置不完整")
        try:
            validate_safe_evidence(evidence)
            validate_safe_evidence({"prompt": system_prompt})
        except ValueError as exc:
            return ProviderResult(False, error_code="DLP_BLOCK", error_message=str(exc))
        selected_model = model or self.settings.model
        safe_serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
        payload_hash = sha256_bytes(safe_serialized)
        if self._budget_guard:
            reason = self._budget_guard(max_tokens)
            if reason:
                return ProviderResult(
                    False,
                    error_code="PROVIDER_BUDGET_EXCEEDED",
                    error_message=reason,
                    model=selected_model,
                    payload_hash=payload_hash,
                )
        if self._request_callback:
            self._request_callback(purpose, evidence, selected_model)
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(45, connect=10)}
        if self.settings.proxy:
            client_kwargs["proxy"] = self.settings.proxy
        if self.settings.ca_cert:
            client_kwargs["verify"] = self.settings.ca_cert
        try:
            with self._client_factory(**client_kwargs) as client:
                response = client.post(
                    self.endpoint(),
                    headers=self._headers(),
                    json=self._body(system_prompt, evidence, selected_model, max_tokens),
                )
                response.raise_for_status()
                content, usage = self._response(response.json())
            if self._usage_callback:
                self._usage_callback(int(usage.get("total_tokens") or 0), selected_model)
            return ProviderResult(True, content, model=selected_model, usage=usage, payload_hash=payload_hash)
        except httpx.HTTPStatusError as exc:
            code = "PROVIDER_HTTP_ERROR"
            if exc.response.status_code in {401, 403}:
                code = "PROVIDER_AUTH_FAILED"
            elif exc.response.status_code == 429:
                code = "PROVIDER_RATE_LIMITED"
            return ProviderResult(
                False,
                error_code=code,
                error_message=f"HTTP {exc.response.status_code}",
                model=selected_model,
                payload_hash=payload_hash,
            )
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            return ProviderResult(
                False,
                error_code="PROVIDER_REQUEST_FAILED",
                error_message=str(exc)[:300],
                model=selected_model,
                payload_hash=payload_hash,
            )

    def complete_json(
        self,
        system_prompt: str,
        evidence: dict[str, Any],
        model: str | None = None,
        purpose: str = "agent",
    ) -> tuple[dict[str, Any] | None, ProviderResult]:
        result = self.complete(system_prompt, evidence, model=model, purpose=purpose)
        if not result.ok:
            return None, result
        try:
            return _parse_json_content(result.content), result
        except (ValueError, json.JSONDecodeError) as exc:
            result.ok = False
            result.error_code = "PROVIDER_SCHEMA_INVALID"
            result.error_message = str(exc)[:300]
            return None, result

    def connectivity_check(self) -> ProviderResult:
        return self.complete(
            "Return exactly a JSON object: {\"status\":\"ok\"}.",
            {"health_check": True, "schema_version": "provider-health/v1"},
            max_tokens=32,
            purpose="connectivity_check",
            allow_disabled_for_test=True,
        )
