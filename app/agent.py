from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from .config import load_config, provider_key


SENSITIVE_EVIDENCE_KEYS = {
    "raw_rows",
    "raw_data",
    "customer_records",
    "customer_level_records",
    "original_column_names",
    "local_path",
    "dataset_path",
    "model_path",
}


# Provider presets are convenience defaults only. Users can still override the
# endpoint and model for an internal gateway; the wire protocol remains an
# explicit setting so a Kimi/DeepSeek Anthropic-compatible endpoint is not
# accidentally called with OpenAI headers.
PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "custom": {
        "label": "自定义 Provider",
        "formats": ["openai", "anthropic"],
        "defaults": {"openai": {"base_url": "", "model": ""}, "anthropic": {"base_url": "", "model": ""}},
    },
    "deepseek": {
        "label": "DeepSeek",
        "formats": ["openai", "anthropic"],
        "defaults": {
            "openai": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
            "anthropic": {"base_url": "https://api.deepseek.com/anthropic", "model": "deepseek-v4-flash"},
        },
    },
    "kimi": {
        "label": "Kimi 开放平台",
        "formats": ["openai"],
        "defaults": {"openai": {"base_url": "https://api.moonshot.ai/v1", "model": "kimi-k2.5"}},
    },
    "kimi_code": {
        "label": "Kimi Code",
        "formats": ["openai", "anthropic"],
        "defaults": {
            "openai": {"base_url": "https://api.kimi.com/coding/v1", "model": "kimi-for-coding"},
            "anthropic": {"base_url": "https://api.kimi.com/coding/", "model": "kimi-for-coding"},
        },
    },
    "openai": {
        "label": "OpenAI",
        "formats": ["openai"],
        "defaults": {"openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5"}},
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "formats": ["anthropic"],
        "defaults": {"anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-opus-4-6"}},
    },
}


def provider_presets() -> Dict[str, Dict[str, Any]]:
    """Return a JSON-safe copy for the settings UI without any secrets."""
    return json.loads(json.dumps(PROVIDER_PRESETS, ensure_ascii=False))


@dataclass
class ProviderResult:
    ok: bool
    content: str = ""
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    model: str = ""
    usage: Optional[Dict[str, Any]] = None


def _assert_safe_payload(value: Any, key_path: str = "payload") -> None:
    """Fail closed before any payload can cross the Provider boundary."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in SENSITIVE_EVIDENCE_KEYS:
                raise ValueError(f"DLP_BLOCK: forbidden evidence key at {key_path}.{key}")
            _assert_safe_payload(item, f"{key_path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe_payload(item, f"{key_path}[{index}]")


def _extract_message_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("PROVIDER_EMPTY_RESPONSE: missing choices")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise ValueError("PROVIDER_EMPTY_RESPONSE: missing message content")
    return content.strip()


def _parse_json_content(content: str) -> Dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("PROVIDER_SCHEMA_INVALID: expected a JSON object")
    return parsed


class ProviderGateway:
    """Local egress boundary for OpenAI Chat and Anthropic Messages APIs.

    DeepSeek and Kimi Code expose both protocols, while the Kimi Open Platform
    and the native OpenAI/Anthropic services use their respective protocol.
    The domain layer never receives the API key and never sends a DataFrame. A
    configured key alone does not enable network calls; the user must explicitly
    turn on ``llm_enabled`` in the local settings page.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        client_factory: Any = None,
        budget_guard: Optional[Callable[[int], Optional[str]]] = None,
        usage_callback: Optional[Callable[[int, str], None]] = None,
        request_callback: Optional[Callable[[str, Dict[str, Any], str], None]] = None,
        purpose: str = "agent",
        api_key: Optional[str] = None,
    ):
        self.config = config or load_config()
        self._api_key_override = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._client_factory = client_factory or httpx.Client
        self._budget_guard = budget_guard
        self._usage_callback = usage_callback
        self._request_callback = request_callback
        self._purpose = purpose

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("llm_enabled"))

    @property
    def api_format(self) -> str:
        value = str(self.config.get("api_format") or "").strip().lower()
        if value in {"openai", "anthropic"}:
            return value
        provider = str(self.config.get("provider") or "").strip().lower()
        return "anthropic" if provider in {"anthropic", "claude"} else "openai"

    def _api_key(self) -> str:
        return self._api_key_override or provider_key()

    @property
    def configured(self) -> bool:
        return bool(self._api_key() and self.config.get("base_url") and self.config.get("model"))

    def _endpoint(self) -> str:
        base_url = str(self.config.get("base_url") or "").strip().rstrip("/")
        if self.api_format == "openai":
            return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        if base_url.endswith("/messages"):
            return base_url
        return f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"

    def status(self) -> Dict[str, Any]:
        configured = self.configured
        active = configured and self.enabled
        if active:
            mode = "external-enabled"
            message = "外部 API 已启用；请求只允许携带 SafeEvidence。"
        elif configured:
            mode = "deterministic-fallback"
            message = "API 参数已保存但未启用网络调用，当前仍使用本地确定性流程。"
        else:
            mode = "deterministic-fallback"
            message = "未配置外部 API，Agent 使用本地确定性建议。"
        return {
            "configured": configured,
            "enabled": active,
            "provider": self.config.get("provider", "custom"),
            "api_format": self.api_format,
            "endpoint": self._endpoint() if self.config.get("base_url") else "",
            "model": self.config.get("model", ""),
            "reviewer_model": self.config.get("reviewer_model", ""),
            "mode": mode,
            "message": message,
        }

    def _request_body(self, system_prompt: str, user_payload: Dict[str, Any], model: str, max_tokens: int) -> Dict[str, Any]:
        user_content = json.dumps(user_payload, ensure_ascii=False, sort_keys=True)
        if self.api_format == "anthropic":
            return {
                "model": model,
                "system": system_prompt,
                "max_tokens": max_tokens,
                "temperature": 0,
                "messages": [{"role": "user", "content": user_content}],
            }
        return {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

    def _headers(self) -> Dict[str, str]:
        if self.api_format == "anthropic":
            return {
                "x-api-key": self._api_key(),
                "anthropic-version": str(self.config.get("anthropic_version") or "2023-06-01"),
                "Content-Type": "application/json",
            }
        return {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}

    def _extract_response(self, payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        if self.api_format == "anthropic":
            blocks = payload.get("content") or []
            content = "".join(str(item.get("text", "")) for item in blocks if isinstance(item, dict) and item.get("type") == "text")
            if not content.strip():
                raise ValueError("PROVIDER_EMPTY_RESPONSE: missing Anthropic text content")
            usage = dict(payload.get("usage") or {})
            if "total_tokens" not in usage:
                usage["total_tokens"] = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            return content.strip(), usage
        usage = dict(payload.get("usage") or {})
        return _extract_message_content(payload), usage

    @staticmethod
    def _http_error(exc: httpx.HTTPStatusError) -> tuple[str, str]:
        status_code = exc.response.status_code
        error_code = "PROVIDER_HTTP_ERROR"
        if status_code in {401, 403}:
            error_code = "PROVIDER_AUTH_FAILED"
        elif status_code == 429:
            error_code = "PROVIDER_RATE_LIMITED"
        message = f"HTTP {status_code}"
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or message)
                elif payload.get("message"):
                    message = str(payload["message"])
        except (ValueError, TypeError):
            pass
        return error_code, message[:300]

    def complete(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> ProviderResult:
        if not self.enabled or not self.configured:
            return ProviderResult(ok=False, error_code="PROVIDER_DISABLED", error_message="Provider 未启用或配置不完整")
        try:
            _assert_safe_payload(user_payload)
            _assert_safe_payload({"system_prompt": system_prompt})
        except ValueError as exc:
            return ProviderResult(ok=False, error_code="DLP_BLOCK", error_message=str(exc))

        token_budget = int(self.config.get("run_token_budget") or 0)
        selected_max_tokens = max_tokens or (min(token_budget, 4096) if token_budget else 2048)
        if self._budget_guard:
            budget_error = self._budget_guard(selected_max_tokens)
            if budget_error:
                return ProviderResult(ok=False, error_code="PROVIDER_BUDGET_EXCEEDED", error_message=budget_error)
        selected_model = str(model or self.config.get("model"))
        body = self._request_body(system_prompt, user_payload, selected_model, selected_max_tokens)
        headers = self._headers()
        if self._request_callback:
            try:
                self._request_callback(self._purpose, user_payload, selected_model)
            except Exception:
                # Request logging must never change the provider safety path.
                pass
        timeout = httpx.Timeout(30.0, connect=10.0)
        client_kwargs: Dict[str, Any] = {"timeout": timeout}
        proxy = str(self.config.get("proxy") or "").strip()
        if proxy:
            client_kwargs["proxy"] = proxy
        ca_cert = str(self.config.get("ca_cert") or "").strip()
        if ca_cert:
            client_kwargs["verify"] = ca_cert
        try:
            with self._client_factory(**client_kwargs) as client:
                response = client.post(self._endpoint(), headers=headers, json=body)
                response.raise_for_status()
                response_payload = response.json()
                content, usage = self._extract_response(response_payload)
                if self._usage_callback:
                    try:
                        self._usage_callback(int(usage.get("total_tokens") or 0), selected_model)
                    except (TypeError, ValueError):
                        pass
                return ProviderResult(ok=True, content=content, model=selected_model, usage=usage)
        except httpx.HTTPStatusError as exc:
            code, message = self._http_error(exc)
            return ProviderResult(ok=False, error_code=code, error_message=message, model=selected_model)
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            return ProviderResult(ok=False, error_code="PROVIDER_REQUEST_FAILED", error_message=str(exc)[:300], model=selected_model)

    def complete_json(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        model: Optional[str] = None,
    ) -> tuple[Optional[Dict[str, Any]], ProviderResult]:
        result = self.complete(system_prompt, user_payload, model=model)
        if not result.ok:
            return None, result
        try:
            return _parse_json_content(result.content), result
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            result.ok = False
            result.error_code = "PROVIDER_SCHEMA_INVALID"
            result.error_message = str(exc)[:300]
            return None, result

    def connectivity_check(self) -> ProviderResult:
        return self.complete(
            "Return exactly a JSON object with one key, status, whose value is ok.",
            {"health_check": True, "schema_version": "provider-health/v1"},
            max_tokens=32,
        )

def alias_fields(profile: Dict[str, Any]) -> Dict[str, str]:
    return {item["name"]: f"f_{index:04d}" for index, item in enumerate(profile.get("columns_detail", []), start=1)}


def build_safe_evidence(profile: Dict[str, Any], target: Dict[str, Any], selection: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    aliases = alias_fields(profile)
    fields = []
    for item in profile.get("columns_detail", []):
        dictionary = item.get("dictionary") or {}
        fields.append(
            {
                "alias": aliases.get(item["name"]),
                "type": item.get("type"),
                "missing_rate": item.get("missing_rate"),
                "unique_count": item.get("unique_count"),
                "target_candidate": item.get("target_candidate", False),
                "dictionary_role": dictionary.get("role"),
                "semantic_metadata_available": bool(dictionary),
            }
        )
    evidence = {
        "schema_version": "risk-safe-evidence/v1",
        "rows": profile.get("rows"),
        "columns": profile.get("columns"),
        "target": {
            "alias": aliases.get(target.get("target"), "target"),
            "positive_count": target.get("positive_count"),
            "negative_count": target.get("negative_count"),
            "positive_rate": target.get("positive_rate"),
            "contract_ok": target.get("contract_ok"),
        },
        "fields": fields,
        "suppression": {"min_group_size": 50, "raw_rows_included": False, "customer_records_included": False},
        "provider_payload_policy": "no_raw_rows_or_original_column_names",
    }
    if selection:
        evidence["selection"] = {
            "selected_aliases": [aliases.get(column, column) for column in selection.get("selected", [])],
            "funnel": selection.get("funnel", {}),
        }
    return evidence


def _alias_text(text: str, profile: Optional[Dict[str, Any]]) -> str:
    """Replace local field names before a conversational turn crosses the API boundary."""
    result = str(text or "")[:2000]
    for original, alias in sorted(alias_fields(profile or {}).items(), key=lambda item: len(item[0]), reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])"
        result = re.sub(pattern, alias, result)
    return result


def _chat_dlp_reason(text: str) -> Optional[str]:
    """Detect common pasted identifiers before an optional external chat call.

    Chat is persisted locally, but free-form user text is not SafeEvidence by
    default.  We fail closed for high-confidence secrets and long identifiers
    rather than trying to guess whether a number is a harmless business value.
    """
    value = str(text or "")
    if re.search(r"(?i)\b(?:bearer\s+|sk-|api[_ -]?key\s*[:=])[^\s,;]{12,}", value):
        return "检测到疑似凭据"
    if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", value):
        return "检测到疑似邮箱"
    if re.search(r"(?<!\d)(?:\d[ -]?){11,19}(?!\d)", value):
        return "检测到疑似手机号或卡号"
    return None


def _chat_intent(text: str) -> Optional[str]:
    """Map a small, explicit set of safe questions to provider intents.

    The original free-form sentence is deliberately never needed by the
    Provider.  Unknown questions stay local instead of being heuristically
    scrubbed and then sent over the network.
    """
    value = str(text or "").lower()
    intent_patterns = (
        ("explain_evidence", ("为什么", "原因", "排除", "复核", "explain", "why")),
        ("next_step", ("下一步", "接下来", "怎么做", "需要确认", "next step")),
        ("model_comparison", ("模型", "auc", "ks", "冠军", "比较", "model")),
        ("target_contract", ("目标", "y字段", "正类", "负类", "target", "0/1")),
        ("split_plan", ("切分", "训练集", "验证集", "oot", "留出", "split")),
        ("report_delivery", ("报告", "评分卡", "产物", "导出", "report", "scorecard")),
        ("segment_analysis", ("画像", "坏样本率", "分组", "维度", "分析", "segment")),
    )
    for intent, patterns in intent_patterns:
        if any(pattern in value for pattern in patterns):
            return intent
    return None


def _restore_aliases(text: str, profile: Optional[Dict[str, Any]]) -> str:
    result = str(text or "")
    aliases = alias_fields(profile or {})
    for original, alias in sorted(aliases.items(), key=lambda item: len(item[1]), reverse=True):
        result = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", original, result)
    return result


def _safe_chat_context(run_state: Dict[str, Any]) -> Dict[str, Any]:
    profile = run_state.get("profile") or {}
    target = run_state.get("target") or {}
    plan = run_state.get("plan") or {}
    return {
        "schema_version": "risk-chat-context/v1",
        "status": run_state.get("status"),
        "phase": run_state.get("phase"),
        "run_kind": run_state.get("run_kind", "formal"),
        "evidence": build_safe_evidence(profile, target, run_state.get("selection")),
        "plan": _safe_plan_payload(plan, profile, target) if plan else None,
        "quality": {
            "rows": (run_state.get("quality") or {}).get("rows"),
            "columns": (run_state.get("quality") or {}).get("columns"),
            "duplicate_rows": (run_state.get("quality") or {}).get("duplicate_rows"),
        },
        "next_step": run_state.get("next_step"),
    }


def answer_chat(message: str, run_state: Optional[Dict[str, Any]], gateway: ProviderGateway) -> Dict[str, Any]:
    """Answer a project-bound conversational turn without turning chat into a second workflow engine."""
    text = str(message or "").strip()
    if not text:
        raise ValueError("CHAT_MESSAGE_EMPTY")
    if len(text) > 2000:
        raise ValueError("CHAT_MESSAGE_TOO_LONG")
    run_state = run_state or {}
    profile = run_state.get("profile") or {}
    safe_context = _safe_chat_context(run_state)
    next_actions: List[str] = []
    status = run_state.get("status")
    phase = run_state.get("phase")
    if not run_state:
        fallback = "请先导入 CSV/XLSX；我会在本机完成画像、Y 契约、变量筛选、模型比较和报告。"
        next_actions.append("导入数据集")
    elif status == "awaiting_confirmation":
        if phase == "cleaning" and (run_state.get("cleaning") or {}).get("requires_confirmation") and not (run_state.get("cleaning") or {}).get("execution"):
            fallback = "当前停在数据清洗确认节点。请批准清洗动作，或明确选择跳过业务性清洗，然后再确认 Y、切分和模型。"
            next_actions.extend(["批准清洗", "跳过业务性清洗", "确认建模方案"])
        else:
            fallback = "当前方案已完成审核，等待确认 Y、样本切分、排除字段和候选模型。确认后才会开始训练。"
            next_actions.append("确认建模方案")
    elif status == "succeeded":
        champion = ((run_state.get("report") or {}).get("champion") or {}).get("name")
        fallback = f"本次 Run 已完成{f'，当前冠军为 {champion}' if champion else ''}。可以查看报告、导出 Trace，或派生一个隔离的 what-if 实验。"
        next_actions.extend(["查看报告", "派生 what-if 实验"])
    elif status in {"blocked", "failed"}:
        fallback = "当前 Run 被阻断或失败。请先查看时间线里的结构化错误和 Reviewer 发现，再修改方案或创建新 Run；历史产物不会被覆盖。"
        next_actions.append("查看结构化问题")
    else:
        fallback = "本地 Worker 正在按当前节点运行；时间线会持续显示工具、Agent 和 Reviewer 的反馈。"
        next_actions.extend(["查看时间线", "暂停或取消 Run"])

    provider_mode = "deterministic-fallback"
    response_text = fallback
    provider_call: Dict[str, Any] = {"attempted": False, "ok": False, "error_code": "PROVIDER_DISABLED"}
    dlp_reason = _chat_dlp_reason(text)
    intent = _chat_intent(text)
    if gateway.enabled and gateway.configured and dlp_reason:
        provider_call = {"attempted": False, "ok": False, "error_code": "CHAT_DLP_BLOCK", "reason": dlp_reason}
        response_text = f"{fallback} 为保护数据安全，本轮消息未发送到外部 API（{dlp_reason}）。"
    elif gateway.enabled and gateway.configured and not intent:
        provider_call = {"attempted": False, "ok": False, "error_code": "CHAT_TEXT_LOCAL_ONLY"}
        response_text = f"{fallback} 为保护数据安全，未将这段自由文本发送到外部 API；你可以继续使用本机流程或改用结构化问题。"
    elif gateway.enabled and gateway.configured:
        provider_mode = "external-enabled"
        result = gateway.complete(
            "你是风控建模工作台中的项目协作 Agent。只根据匿名上下文回答，不能编造指标或客户信息。"
            "如果问题需要改变 Y、切分、清洗、变量或模型，明确提示用户走结构化确认入口；"
            "不要声称已经执行未发生的动作。回答简洁、带证据边界。",
            {"intent": intent, "context": safe_context},
            model=gateway.config.get("model"),
            max_tokens=600,
        )
        provider_call = {"attempted": True, "ok": result.ok, "error_code": result.error_code, "model": result.model}
        if result.ok and result.content:
            response_text = _restore_aliases(result.content, profile)
        elif result.error_code:
            provider_mode = "deterministic-fallback"
    return {
        "schema_version": "risk-chat-turn/v1",
        "content": response_text,
        "agent": "main-agent",
        "provider_mode": provider_mode,
        "provider_call": provider_call,
        "next_actions": next_actions,
        "evidence_refs": ["run.state.profile", "run.state.plan"] if run_state else [],
    }


def generate_report_narrative(report: Dict[str, Any], gateway: Optional[ProviderGateway] = None) -> Dict[str, Any]:
    """Draft report prose from deterministic artifacts; never let an LLM invent metrics."""
    profile = report.get("profile") or {}
    target = (report.get("plan") or {}).get("target") or (profile.get("target_candidates") or [None])[0]
    target_summary = {
        "positive_rate": ((report.get("target") or {}).get("positive_rate")),
        "contract_ok": ((report.get("target") or {}).get("contract_ok")),
    }
    metrics = []
    for item in report.get("metrics", []):
        validation = item.get("validation") or {}
        metrics.append(
            {
                "model": item.get("name"),
                "status": item.get("status"),
                "validation_roc_auc": validation.get("roc_auc"),
                "validation_ks": validation.get("ks"),
                "validation_pr_auc": validation.get("pr_auc"),
                "validation_brier": validation.get("brier"),
            }
        )
    selection = report.get("selection") or {}
    funnel = selection.get("funnel") or {}
    quality = report.get("quality") or {}
    champion = report.get("champion") or {}
    stability = report.get("stability") or {}
    summary = {
        "schema_version": "risk-report-narrative-input/v1",
        "run_kind": (report.get("manifest") or {}).get("run_kind", "formal"),
        "rows": profile.get("rows"),
        "columns": profile.get("columns"),
        "target_contract": target_summary,
        "split": report.get("split") or {},
        "quality": {
            "duplicate_rows": quality.get("duplicate_rows"),
            "numeric_count": len(quality.get("numeric", [])),
            "categorical_count": len(quality.get("categorical", [])),
        },
        "selection_funnel": funnel,
        "candidate_metrics": metrics,
        "champion": {
            "name": champion.get("name"),
            "validation": {
                "roc_auc": (champion.get("validation") or {}).get("roc_auc"),
                "ks": (champion.get("validation") or {}).get("ks"),
                "pr_auc": (champion.get("validation") or {}).get("pr_auc"),
            },
        },
        "baseline_available": bool(report.get("baseline")),
        "stability_review_count": sum(
            1
            for item in stability.get("features", [])
            if (item.get("validation") or {}).get("review_flag") in {"review", "high"}
            or (item.get("oot") or {}).get("review_flag") in {"review", "high"}
        ),
        "code_review_verdict": (report.get("code_review") or {}).get("verdict"),
        "fact_boundary": "离线实验结果，不代表生产效果或合规结论",
    }
    champion_name = champion.get("name") or "暂无"
    validation = champion.get("validation") or {}
    experiment_prefix = "这是 what-if 实验，默认未审核；" if summary["run_kind"] == "experiment" else ""
    sections = [
        {
            "id": "executive_summary",
            "title": "执行摘要",
            "text": f"{experiment_prefix}本次运行在本机完成 {profile.get('rows', 0):,} 行、{profile.get('columns', 0):,} 个字段的分析，比较 {len(metrics)} 个候选模型。当前冠军建议为 {champion_name}，验证集 ROC-AUC 为 {validation.get('roc_auc', '—')}、KS 为 {validation.get('ks', '—')}。",
            "source": "deterministic-artifact",
            "evidence_refs": ["profile.rows", "metrics", "champion.validation"],
        },
        {
            "id": "data_and_target",
            "title": "数据与目标",
            "text": f"数据只在本机读取；目标字段为 {target or '待确认'}，0/1 契约状态为 {target_summary.get('contract_ok', '—')}，正类比例为 {target_summary.get('positive_rate', '—')}。数据质量包含 {quality.get('duplicate_rows', 0)} 行重复记录、{len(quality.get('numeric', []))} 个数值字段和 {len(quality.get('categorical', []))} 个类别字段。",
            "source": "deterministic-artifact",
            "evidence_refs": ["profile", "target", "quality"],
        },
        {
            "id": "selection_and_validation",
            "title": "变量筛选与验证",
            "text": f"变量筛选在训练分区拟合 IV/缺失等规则，字段漏斗从 {funnel.get('input_features', '—')} 个字段收敛到 {funnel.get('final', '—')} 个字段。验证协议为 train-only 拟合、validation 选择、OOT 一次评估；OOT 不参与调参或冠军选择。",
            "source": "deterministic-artifact",
            "evidence_refs": ["selection.funnel", "selection_rule", "split"],
        },
        {
            "id": "model_comparison",
            "title": "模型比较",
            "text": f"候选模型统一使用冻结数据和验证协议比较。冠军建议为 {champion_name}；模型表现应结合校准、稳定性、解释性和业务审批共同判断，不能仅凭单一离线指标上线。",
            "source": "deterministic-artifact",
            "evidence_refs": ["metrics", "champion", "stability"],
        },
        {
            "id": "limitations",
            "title": "限制与下一步",
            "text": f"当前有 {summary['stability_review_count']} 个变量需要稳定性复核；代码审核结论为 {(report.get('code_review') or {}).get('verdict', '—')}。本报告是指定数据和切分下的离线实验，不代表生产效果、合规结论或自动授信决策。",
            "source": "deterministic-artifact",
            "evidence_refs": ["stability", "code_review", "manifest.fact_boundary"],
        },
    ]
    provider_call = {"attempted": False, "ok": False, "error_code": "PROVIDER_DISABLED"}
    if gateway and gateway.enabled and gateway.configured:
        external, result = gateway.complete_json(
            "你是银行风控报告叙事 Agent。只能根据给定匿名结构化证据写报告段落，不能修改或编造数字。"
            "返回 JSON：{\"sections\":[{\"id\":\"executive_summary|data_and_target|selection_and_validation|model_comparison|limitations\",\"text\":\"...\"}]}。"
            "每段不超过 180 个汉字，明确离线实验边界，不得出现原始字段名。",
            {"report_evidence": summary},
            model=gateway.config.get("model"),
        )
        provider_call = {"attempted": True, "ok": result.ok, "error_code": result.error_code, "model": result.model}
        external_sections = (external or {}).get("sections") if isinstance(external, dict) else None
        if result.ok and isinstance(external_sections, list):
            by_id = {item.get("id"): item for item in sections}
            for item in external_sections[:5]:
                if not isinstance(item, dict) or item.get("id") not in by_id or not str(item.get("text") or "").strip():
                    continue
                by_id[item["id"]]["text"] = str(item["text"])[:1000]
                by_id[item["id"]]["source"] = "provider-draft"
            sections = list(by_id.values())
    return {
        "schema_version": "risk-report-narrative/v1",
        "sections": sections,
        "provider_call": provider_call,
        "locked": False,
        "fact_boundary": summary["fact_boundary"],
    }


def _safe_plan_payload(plan: Dict[str, Any], profile: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    aliases = alias_fields(profile)
    screening = dict(plan.get("screening") or {})
    screening["excluded_columns"] = [aliases.get(column, column) for column in screening.get("excluded_columns", [])]
    return {
        "schema_version": "risk-safe-plan/v1",
        "target_alias": aliases.get(plan.get("target"), "target"),
        "time_column_alias": aliases.get(plan.get("time_column_suggestion")) if plan.get("time_column_suggestion") else None,
        "positive_value": plan.get("positive_value"),
        "negative_value": plan.get("negative_value"),
        "split": plan.get("split"),
        "models": plan.get("models"),
        "screening": screening,
        "mode": plan.get("mode"),
        "target_contract": {
            "positive_count": target.get("positive_count"),
            "negative_count": target.get("negative_count"),
            "positive_rate": target.get("positive_rate"),
            "contract_ok": target.get("contract_ok"),
        },
    }


def propose_plan(profile: Dict[str, Any], target: Dict[str, Any], mode: str, gateway: ProviderGateway) -> Dict[str, Any]:
    candidates = profile.get("target_candidates", [])
    selected_target = target.get("target") or (candidates[0] if candidates else None)
    time_candidates = [item["name"] for item in profile.get("columns_detail", []) if item.get("type") == "datetime" or re.search(r"date|time|month|申请|时间", item["name"], re.I)]
    plan = {
        "schema_version": "risk-model-plan/v1",
        "target": selected_target,
        "positive_value": 1,
        "negative_value": 0,
        "target_meaning": "待用户确认：1 代表坏样本还是好样本",
        "time_column_suggestion": time_candidates[0] if time_candidates else None,
        "split": {"method": "time_holdout" if time_candidates else "stratified_holdout", "train": 0.6, "validation": 0.2, "oot": 0.2},
        "baseline_column": None,
        "models": ["woe_logistic_scorecard", "logistic_regression", "random_forest", "hist_gradient_boosting", "xgboost"],
        "screening": {"max_features": 50, "missing_rate_max": 0.95, "min_iv": 0.005, "train_only": True},
        "mode": mode,
        "provider": gateway.status(),
    }
    if getattr(gateway, "enabled", False) and getattr(gateway, "configured", False):
        advisory, provider_result = gateway.complete_json(
            """你是风控分析 Agent。只根据匿名 SafeEvidence 提供结构化建议。
不要发明字段原名、客户记录或业务结论。返回 JSON：
{\"risk_flags\":[{\"code\":\"...\",\"message\":\"...\"}],\"questions\":[\"...\"]}""",
            {"evidence": build_safe_evidence(profile, target), "plan": _safe_plan_payload(plan, profile, target)},
            model=getattr(gateway, "config", {}).get("model"),
        )
        plan["agent_advisory"] = advisory or {"risk_flags": [], "questions": []}
        plan["provider_call"] = {
            "attempted": True,
            "ok": provider_result.ok,
            "error_code": provider_result.error_code,
            "model": provider_result.model,
        }
    else:
        plan["agent_advisory"] = {"risk_flags": [], "questions": []}
        plan["provider_call"] = {"attempted": False, "ok": False, "error_code": "PROVIDER_DISABLED"}
    return plan


def review_plan(
    plan: Dict[str, Any],
    profile: Dict[str, Any],
    target: Dict[str, Any],
    gateway: Optional[ProviderGateway] = None,
) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    if not target.get("contract_ok"):
        findings.append({"code": "TARGET_CONTRACT_FAILED", "severity": "block", "message": "Y 尚未满足明确 0/1 双类别契约。"})
    elif min(int(target.get("positive_count", 0)), int(target.get("negative_count", 0))) < 5:
        findings.append(
            {
                "code": "TARGET_CLASS_TOO_SMALL",
                "severity": "block",
                "message": "Y 的某一类别少于 5 个样本，无法可靠完成分层切分。",
                "details": {"positive_count": target.get("positive_count"), "negative_count": target.get("negative_count")},
            }
        )
    elif min(float(target.get("positive_rate", 0.0)), 1.0 - float(target.get("positive_rate", 0.0))) < 0.01:
        findings.append(
            {
                "code": "SEVERE_CLASS_IMBALANCE",
                "severity": "warn",
                "message": "正类比例低于 1%，需要结合业务成本解释指标；训练会使用类别不平衡策略。",
            }
        )
    if any(item.get("severity") == "block" for item in profile.get("warnings", [])):
        findings.extend(item for item in profile["warnings"] if item.get("severity") == "block")
    if not plan.get("target"):
        findings.append({"code": "TARGET_NOT_SELECTED", "severity": "block", "message": "没有可用于建模的 Y。"})
    result: Dict[str, Any] = {
        "verdict": "block" if findings else "pass",
        "findings": findings,
        "reviewer": "deterministic-reviewer-v1",
        "message": "未发现阻断问题" if not findings else "需要先处理结构化阻断问题",
    }
    if gateway and getattr(gateway, "enabled", False) and getattr(gateway, "configured", False):
        external, provider_result = gateway.complete_json(
            """你是独立 Reviewer Agent。审核匿名的建模方案是否存在目标契约、切分污染、泄漏或资源风险。
只返回 JSON：{\"verdict\":\"pass|warn|block\",\"findings\":[{\"code\":\"...\",\"severity\":\"warn|block\",\"message\":\"...\"}]}。
不要输出字段原名或客户数据。""",
            {"evidence": build_safe_evidence(profile, target), "plan": _safe_plan_payload(plan, profile, target)},
            model=getattr(gateway, "config", {}).get("reviewer_model") or getattr(gateway, "config", {}).get("model"),
        )
        if external:
            external_verdict = external.get("verdict") if external.get("verdict") in {"pass", "warn", "block"} else "warn"
            external_findings = external.get("findings") if isinstance(external.get("findings"), list) else []
            result["external_review"] = {"verdict": external_verdict, "findings": external_findings[:20]}
            if external_verdict == "block":
                result["verdict"] = "block"
                result["findings"].extend(external_findings[:20])
        result["provider_call"] = {
            "attempted": True,
            "ok": provider_result.ok,
            "error_code": provider_result.error_code,
            "model": provider_result.model,
        }
    return result


def _alias_code(code: str, profile: Optional[Dict[str, Any]]) -> str:
    if not profile:
        return code
    aliased = code
    for original, alias in sorted(alias_fields(profile).items(), key=lambda item: len(item[0]), reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(original)}(?![A-Za-z0-9_])"
        aliased = re.sub(pattern, alias, aliased)
    return aliased


def review_generated_code(
    code: str,
    gateway: Optional[ProviderGateway] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    seen_codes = set()

    def add(code_name: str, message: str, line: Optional[int] = None) -> None:
        if code_name not in seen_codes:
            finding: Dict[str, Any] = {"code": code_name, "severity": "block", "message": message}
            if line:
                finding["location"] = {"line": line}
            findings.append(finding)
            seen_codes.add(code_name)

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        add("CODE_SYNTAX_INVALID", f"生成代码无法解析：第 {exc.lineno or '?'} 行存在语法错误。", exc.lineno)
        tree = None
    allowed_import_roots = {"math", "typing", "numpy", "pandas", "sklearn", "xgboost"}
    blocked_import_roots = {"os", "subprocess", "requests", "httpx", "urllib", "socket", "ctypes", "pickle", "cloudpickle"}
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
                if any(root in blocked_import_roots for root in roots):
                    add("DANGEROUS_IMPORT", "生成代码导入了网络、Shell、凭据或不受信序列化模块。", node.lineno)
                if any(root not in allowed_import_roots for root in roots):
                    add("DEPENDENCY_NOT_ALLOWLISTED", "生成代码包含未登记的第三方或系统依赖。", node.lineno)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in blocked_import_roots:
                    add("DANGEROUS_IMPORT", "生成代码导入了网络、Shell、凭据或不受信序列化模块。", node.lineno)
                if root not in allowed_import_roots:
                    add("DEPENDENCY_NOT_ALLOWLISTED", "生成代码包含未登记的第三方或系统依赖。", node.lineno)
            elif isinstance(node, ast.Call):
                function = node.func
                name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ""
                if name in {"eval", "exec", "compile", "__import__", "getattr", "setattr"}:
                    add("DANGEROUS_EXEC", "生成代码包含动态执行或动态加载调用。", node.lineno)
                if name in {"open", "system", "popen", "run", "Popen", "check_call", "check_output"}:
                    add("DANGEROUS_SHELL", "生成代码包含文件、Shell 或子进程调用。", node.lineno)
            elif isinstance(node, ast.Attribute) and node.attr in {"environ", "system", "popen"}:
                add("DANGEROUS_SECRET", "生成代码包含环境变量或系统调用访问。", node.lineno)
    dangerous_text = [
        ("DANGEROUS_NETWORK", r"\b(requests|httpx|urllib|socket)\b|https?://"),
        ("DANGEROUS_SECRET", r"API_KEY|Authorization|os\.environ"),
    ]
    for code_name, pattern in dangerous_text:
        if re.search(pattern, code, flags=re.I):
            add(code_name, "生成代码包含 V1 禁止的网络或凭据访问模式。")
    result: Dict[str, Any] = {
        "verdict": "block" if findings else "pass",
        "findings": findings,
        "checks": ["ast_pattern_scan", "dependency_policy", "generated_code_not_executed"],
        "review_round": 1,
        "reviewer": "deterministic-reviewer-v1",
    }
    if gateway and getattr(gateway, "enabled", False) and getattr(gateway, "configured", False):
        external, provider_result = gateway.complete_json(
            """你是独立代码 Reviewer。检查匿名化后的 Python 复现代码是否有网络、Shell、动态执行、凭据读取、数据泄漏或验证污染。
只返回 JSON：{\"verdict\":\"pass|warn|block\",\"findings\":[{\"code\":\"...\",\"severity\":\"warn|block\",\"message\":\"...\"}]}。""",
            {"code": _alias_code(code, profile), "policy": "generated_code_not_executed"},
            model=getattr(gateway, "config", {}).get("reviewer_model") or getattr(gateway, "config", {}).get("model"),
        )
        result["provider_call"] = {"attempted": True, "ok": provider_result.ok, "error_code": provider_result.error_code, "model": provider_result.model}
        if external:
            external_verdict = external.get("verdict") if external.get("verdict") in {"pass", "warn", "block"} else "warn"
            external_findings = external.get("findings") if isinstance(external.get("findings"), list) else []
            result["external_review"] = {"verdict": external_verdict, "findings": external_findings[:20]}
            if external_verdict == "block":
                result["verdict"] = "block"
                result["findings"].extend(external_findings[:20])
    else:
        result["provider_call"] = {"attempted": False, "ok": False, "error_code": "PROVIDER_DISABLED"}
    return result


def repair_generated_code(
    code: str,
    plan: Dict[str, Any],
    selected: List[str],
    findings: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """Regenerate from the allowlisted template after a code review block.

    The repair worker never edits arbitrary text suggested by an LLM. It
    discards the rejected draft and reconstructs the reproducibility artifact
    from the frozen plan, which is the only safe repair strategy while V1 does
    not execute generated code.
    """
    repaired = generate_reproducible_code(plan, selected, profile)
    return repaired, {
        "strategy": "regenerate_allowlisted_template",
        "finding_codes": [item.get("code") for item in findings[:20]],
        "generated_code_executed": False,
        "source": "frozen_model_plan",
    }


def generate_reproducible_code(plan: Dict[str, Any], selected: List[str], profile: Optional[Dict[str, Any]] = None) -> str:
    profile = profile or {}
    details = {item.get("name"): item.get("type") for item in profile.get("columns_detail", [])}
    numeric = [column for column in selected if details.get(column) == "numeric"]
    categorical = [column for column in selected if column not in numeric]
    columns = ", ".join(repr(column) for column in selected)
    numeric_columns = ", ".join(repr(column) for column in numeric)
    categorical_columns = ", ".join(repr(column) for column in categorical)
    if "woe_logistic_scorecard" in (plan.get("models") or []):
        return f'''"""Generated WOE + Logistic scorecard reference artifact; not executed in V1."""
import math
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

TARGET = {plan.get("target")!r}
FEATURES = [{columns}]
NUMERIC_FEATURES = [{numeric_columns}]
CATEGORICAL_FEATURES = [{categorical_columns}]
BASE_SCORE = 600.0
PDO = 20.0
ODDS = 50.0

def _labels(series: pd.Series, spec: Dict[str, object]) -> pd.Series:
    if spec["kind"] == "numeric":
        return pd.cut(series.astype(float), bins=spec["edges"], include_lowest=True, duplicates="drop").astype("string").fillna("<MISSING>")
    values = series.fillna("<MISSING>").astype(str)
    categories = set(spec.get("categories", []))
    return values.map(lambda value: value if value in categories else "<OTHER>")

def _fit_specs(train: pd.DataFrame, y: pd.Series) -> Dict[str, Dict[str, object]]:
    specs = {{}}
    good_total = max(float((y == 0).sum()), 1.0)
    bad_total = max(float((y == 1).sum()), 1.0)
    for feature in FEATURES:
        series = train[feature]
        if feature in NUMERIC_FEATURES:
            finite = series.dropna().astype(float)
            edges = np.unique(np.nanquantile(finite, np.linspace(0, 1, min(11, finite.nunique() + 1)))).tolist() if finite.nunique() > 1 else [-np.inf, np.inf]
            if len(edges) < 2: edges = [-np.inf, np.inf]
            edges[0], edges[-1] = -np.inf, np.inf
            spec = {{"kind": "numeric", "edges": edges, "categories": []}}
        else:
            values = series.fillna("<MISSING>").astype(str)
            spec = {{"kind": "categorical", "edges": [], "categories": values.value_counts().head(50).index.tolist()}}
        groups = _labels(series, spec)
        table = pd.DataFrame({{"group": groups, "y": y.to_numpy()}}).groupby("group", observed=False)["y"].agg(["count", "sum"])
        mapping = {{}}
        for row in table.itertuples():
            good = float(row.count - row.sum); bad = float(row.sum)
            good_dist = (good + 0.5) / (good_total + 0.5 * len(table))
            bad_dist = (bad + 0.5) / (bad_total + 0.5 * len(table))
            mapping[str(row.Index)] = math.log(good_dist / bad_dist)
        spec["woe"] = mapping
        specs[feature] = spec
    return specs

def fit_reference(frame: pd.DataFrame, train_indices) -> Tuple[LogisticRegression, Dict[str, object]]:
    train = frame.iloc[list(train_indices)]
    y = train[TARGET].astype(int)
    specs = _fit_specs(train, y)
    transformed = pd.DataFrame({{feature: _labels(frame[feature], spec).map(spec["woe"]).fillna(0.0) for feature, spec in specs.items()}})
    model = LogisticRegression(max_iter=500, class_weight="balanced", random_state=42).fit(transformed.iloc[list(train_indices)], y)
    factor = PDO / math.log(2)
    scorecard = {{"base_score": BASE_SCORE, "pdo": PDO, "odds": ODDS, "factor": factor, "specs": specs, "coefficients": dict(zip(FEATURES, model.coef_[0]))}}
    return model, scorecard
'''
    return f'''"""Generated reproducibility artifact; not executed by the product in V1."""
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

TARGET = {plan.get("target")!r}
FEATURES = [{columns}]
NUMERIC_FEATURES = [{numeric_columns}]
CATEGORICAL_FEATURES = [{categorical_columns}]

def fit_reference(frame: pd.DataFrame):
    X = frame[FEATURES].copy()
    y = frame[TARGET].astype(int)
    transformers = []
    if NUMERIC_FEATURES:
        transformers.append(("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), NUMERIC_FEATURES))
    if CATEGORICAL_FEATURES:
        transformers.append(("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), CATEGORICAL_FEATURES))
    preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
    return Pipeline([
        ("preprocess", preprocessor),
        ("model", LogisticRegression(max_iter=500, class_weight="balanced")),
    ]).fit(X, y)
'''
