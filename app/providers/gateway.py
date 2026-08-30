from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.core.config import Settings, SettingsStore
from app.core.errors import normalize_error_code, public_error_message
from app.core.paths import AppPaths
from app.core.security import sanitize_safe_evidence, sha256_bytes, validate_safe_evidence

from .profiles import ProviderProfileStore
from .prompts import CONNECTIVITY_PROMPT
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
    provider_request_id: str = ""
    upstream_request_id: str = ""
    error_type: str | None = None
    http_status: int | None = None
    duration_ms: int = 0
    response_hash: str = ""


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
        request_callback: Callable[[str, dict[str, Any], str], str | None] | None = None,
        result_callback: Callable[[str, ProviderResult], None] | None = None,
        paths: AppPaths | None = None,
        profile_id: str | None = None,
    ):
        self.settings = settings or SettingsStore().load()
        self._api_key_override = (api_key or "").strip()
        self._client_factory = client_factory or httpx.Client
        self._budget_guard = budget_guard
        self._usage_callback = usage_callback
        self._request_callback = request_callback
        self._result_callback = result_callback
        # Each active Provider profile has its own namespaced secret.  The
        # explicit api_key override remains useful for the connection-test
        # endpoint and never gets persisted by the gateway.
        active_profile_id = profile_id or ProviderProfileStore(paths).active_profile_id(
            self.settings
        )
        self._secrets = SecretStore(
            paths,
            profile_id=active_profile_id or self.settings.provider,
        )
        if not self._api_key_override and profile_id is None:
            # A worker can start before the settings page is opened after an
            # upgrade.  Migrate the legacy single-key slot lazily as a second
            # line of defence so that existing users never lose access.
            self._secrets.migrate_legacy()

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
        if active:
            connection_state = "ready"
            message = "API 已配置并启用；仅允许 SafeEvidence 出站。"
        elif not self.configured:
            connection_state = "not_configured"
            message = "API 未连接：配置不完整，当前只使用明确标注的本地降级。"
        else:
            connection_state = "disabled"
            message = "LLM 已关闭：API 配置已保存，当前只使用明确标注的本地降级。"
        return {
            "configured": self.configured,
            "enabled": active,
            "connection_state": connection_state,
            "provider": self.settings.provider,
            "api_format": self.api_format,
            "endpoint": self.endpoint() if self.settings.base_url else "",
            "model": self.settings.model,
            "reviewer_model": self.settings.reviewer_model or self.settings.model,
            "mode": "external-enabled" if active else "deterministic-fallback",
            "message": message,
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
        _defer_result_callback: bool = False,
    ) -> ProviderResult:
        started = time.monotonic()
        if not self.configured or (not self.settings.llm_enabled and not allow_disabled_for_test):
            result = ProviderResult(
                ok=False,
                error_code="PROVIDER_DISABLED",
                error_type="ConfigurationError",
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            return self._finalize_result(result, started, not _defer_result_callback)
        selected_model = model or self.settings.model
        try:
            safe_evidence = sanitize_safe_evidence(evidence)
            validate_safe_evidence({"prompt": system_prompt})
        except ValueError as exc:
            provider_request_id = ""
            blocked_evidence = {
                "schema_version": "provider-blocked-request/v1",
                "blocked": True,
                "block_code": "DLP_BLOCK",
            }
            if self._request_callback:
                provider_request_id = str(
                    self._request_callback(purpose, blocked_evidence, selected_model) or ""
                )
            result = ProviderResult(
                False,
                error_code="DLP_BLOCK",
                error_type=type(exc).__name__,
                model=selected_model,
                payload_hash=sha256_bytes(b"provider-blocked-request/v1"),
                provider_request_id=provider_request_id,
            )
            return self._finalize_result(result, started, not _defer_result_callback)
        safe_serialized = json.dumps(safe_evidence, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        payload_hash = sha256_bytes(safe_serialized)
        if self._budget_guard:
            reason = self._budget_guard(max_tokens)
            if reason:
                provider_request_id = ""
                if self._request_callback:
                    provider_request_id = str(
                        self._request_callback(purpose, safe_evidence, selected_model) or ""
                    )
                result = ProviderResult(
                    False,
                    error_code="PROVIDER_BUDGET_EXCEEDED",
                    error_type="BudgetError",
                    model=selected_model,
                    payload_hash=payload_hash,
                    provider_request_id=provider_request_id,
                )
                return self._finalize_result(result, started, not _defer_result_callback)
        provider_request_id = ""
        if self._request_callback:
            provider_request_id = str(
                self._request_callback(purpose, safe_evidence, selected_model) or ""
            )
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(45, connect=10)}
        if self.settings.proxy:
            client_kwargs["proxy"] = self.settings.proxy
        if self.settings.ca_cert:
            client_kwargs["verify"] = self.settings.ca_cert
        result: ProviderResult | None = None
        response: httpx.Response | None = None
        try:
            with self._client_factory(**client_kwargs) as client:
                response = client.post(
                    self.endpoint(),
                    headers=self._headers(),
                    json=self._body(system_prompt, safe_evidence, selected_model, max_tokens),
                )
                response.raise_for_status()
                content, usage = self._response(response.json())
                headers = getattr(response, "headers", {}) or {}
                upstream_request_id = str(
                    headers.get("x-request-id")
                    or headers.get("request-id")
                    or headers.get("x-amzn-requestid")
                    or ""
                )
            if self._usage_callback:
                self._usage_callback(int(usage.get("total_tokens") or 0), selected_model)
            result = ProviderResult(
                True,
                content,
                model=selected_model,
                usage=usage,
                payload_hash=payload_hash,
                provider_request_id=provider_request_id,
                upstream_request_id=upstream_request_id,
                response_hash=sha256_bytes(content.encode("utf-8")),
            )
        except httpx.HTTPStatusError as exc:
            code = "PROVIDER_HTTP_ERROR"
            if exc.response.status_code in {401, 403}:
                code = "PROVIDER_AUTH_FAILED"
            elif exc.response.status_code == 429:
                code = "PROVIDER_RATE_LIMITED"
            result = ProviderResult(
                False,
                error_code=code,
                model=selected_model,
                payload_hash=payload_hash,
                provider_request_id=provider_request_id,
                upstream_request_id=str(exc.response.headers.get("x-request-id") or ""),
                error_type=type(exc).__name__,
                http_status=exc.response.status_code,
                response_hash=_http_response_hash(exc.response),
            )
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            result = ProviderResult(
                False,
                error_code="PROVIDER_REQUEST_FAILED",
                model=selected_model,
                payload_hash=payload_hash,
                provider_request_id=provider_request_id,
                error_type=type(exc).__name__,
                http_status=getattr(response, "status_code", None),
                response_hash=_http_response_hash(response),
            )
        except Exception as exc:  # provider adapters must still close their trace
            result = ProviderResult(
                False,
                error_code="PROVIDER_REQUEST_FAILED",
                model=selected_model,
                payload_hash=payload_hash,
                provider_request_id=provider_request_id,
                error_type=type(exc).__name__,
                http_status=getattr(response, "status_code", None),
                response_hash=_http_response_hash(response),
            )
        finally:
            if result is None:
                result = ProviderResult(
                    False,
                    error_code="PROVIDER_REQUEST_INTERRUPTED",
                    error_type="InterruptedError",
                    model=selected_model,
                    payload_hash=payload_hash,
                    provider_request_id=provider_request_id,
                    http_status=getattr(response, "status_code", None),
                    response_hash=_http_response_hash(response),
                )
            self._finalize_result(result, started, not _defer_result_callback)
        return self._finalize_result(result, started, False)

    def complete_json(
        self,
        system_prompt: str,
        evidence: dict[str, Any],
        model: str | None = None,
        purpose: str = "agent",
    ) -> tuple[dict[str, Any] | None, ProviderResult]:
        started = time.monotonic()
        result = self.complete(
            system_prompt,
            evidence,
            model=model,
            purpose=purpose,
            _defer_result_callback=True,
        )
        if not result.ok:
            self._finalize_result(result, started, True)
            return None, result
        try:
            payload = _parse_json_content(result.content)
            self._finalize_result(result, started, True)
            return payload, result
        except (ValueError, json.JSONDecodeError) as exc:
            result.ok = False
            result.error_code = "PROVIDER_SCHEMA_INVALID"
            result.error_type = type(exc).__name__
            self._finalize_result(result, started, True)
            return None, result

    def _finalize_result(
        self, result: ProviderResult, started: float, notify: bool
    ) -> ProviderResult:
        result.duration_ms = max(result.duration_ms, int((time.monotonic() - started) * 1000))
        if not result.ok:
            result.error_code = normalize_error_code(
                result.error_code,
                "PROVIDER_REQUEST_FAILED",
            )
            result.error_message = public_error_message(result.error_code)
        if result.content and not result.response_hash:
            result.response_hash = sha256_bytes(result.content.encode("utf-8"))
        if notify and result.provider_request_id and self._result_callback:
            self._result_callback(result.provider_request_id, result)
        return result

    def connectivity_check(self) -> ProviderResult:
        return self.complete(
            CONNECTIVITY_PROMPT.content,
            {"health_check": True, "schema_version": "provider-health/v1"},
            max_tokens=32,
            purpose="connectivity_check",
            allow_disabled_for_test=True,
        )


def _http_response_hash(response: Any) -> str:
    if response is None:
        return ""
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if isinstance(content, bytes) and content:
        return sha256_bytes(content)
    return ""
