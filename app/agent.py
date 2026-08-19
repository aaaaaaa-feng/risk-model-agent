from __future__ import annotations

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
    """OpenAI-compatible Provider boundary with an explicit opt-in switch.

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
    ):
        self.config = config or load_config()
        self._client_factory = client_factory or httpx.Client
        self._budget_guard = budget_guard
        self._usage_callback = usage_callback
        self._request_callback = request_callback
        self._purpose = purpose

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("llm_enabled"))

    @property
    def configured(self) -> bool:
        return bool(provider_key() and self.config.get("base_url") and self.config.get("model"))

    def _endpoint(self) -> str:
        base_url = str(self.config.get("base_url") or "").strip().rstrip("/")
        return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

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
            "provider": self.config.get("provider", "OpenAI-compatible"),
            "model": self.config.get("model", ""),
            "reviewer_model": self.config.get("reviewer_model", ""),
            "mode": mode,
            "message": message,
        }

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
        body = {
            "model": model or self.config.get("model"),
            "temperature": 0,
            "max_tokens": selected_max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
            ],
        }
        headers = {"Authorization": f"Bearer {provider_key()}", "Content-Type": "application/json"}
        if self._request_callback:
            try:
                self._request_callback(self._purpose, user_payload, str(body["model"]))
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
                content = _extract_message_content(response_payload)
                usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
                if self._usage_callback and isinstance(usage, dict):
                    try:
                        self._usage_callback(int(usage.get("total_tokens") or 0), str(body["model"]))
                    except (TypeError, ValueError):
                        pass
                return ProviderResult(
                    ok=True,
                    content=content,
                    model=str(body["model"]),
                    usage=usage,
                )
        except httpx.HTTPStatusError as exc:
            return ProviderResult(ok=False, error_code="PROVIDER_HTTP_ERROR", error_message=f"HTTP {exc.response.status_code}")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return ProviderResult(ok=False, error_code="PROVIDER_REQUEST_FAILED", error_message=str(exc)[:300])

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
    dangerous = [
        ("DANGEROUS_EXEC", r"\b(eval|exec|compile)\s*\("),
        ("DANGEROUS_SHELL", r"\b(os\.system|subprocess|Popen|shell=True)\b"),
        ("DANGEROUS_NETWORK", r"\b(requests|httpx|urllib|socket)\b"),
        ("DANGEROUS_SECRET", r"os\.environ|API_KEY|Authorization"),
    ]
    findings = []
    for code_name, pattern in dangerous:
        if re.search(pattern, code, flags=re.I):
            findings.append({"code": code_name, "severity": "block", "message": "生成代码包含 V1 禁止模式。"})
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
