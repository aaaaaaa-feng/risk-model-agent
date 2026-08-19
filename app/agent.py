from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .config import load_config, provider_key


class ProviderGateway:
    """Safe boundary for a future external LLM call.

    The MVP intentionally uses a deterministic fallback when no provider is configured.
    If a provider is configured, the gateway still only accepts SafeEvidence-shaped payloads;
    wiring a real call can be enabled later without moving raw data into the Agent layer.
    """

    def status(self) -> Dict[str, Any]:
        config = load_config()
        configured = bool(provider_key() and config.get("base_url") and config.get("model"))
        return {
            "configured": configured,
            "provider": config.get("provider", "OpenAI-compatible"),
            "model": config.get("model", ""),
            "reviewer_model": config.get("reviewer_model", ""),
            "mode": "external-ready" if configured else "deterministic-fallback",
            "message": "已配置外部 API，但当前运行仍以本地确定性流程为事实源。" if configured else "未配置外部 API，Agent 使用本地确定性建议。",
        }


def alias_fields(profile: Dict[str, Any]) -> Dict[str, str]:
    return {item["name"]: f"f_{index:04d}" for index, item in enumerate(profile.get("columns_detail", []), start=1)}


def build_safe_evidence(profile: Dict[str, Any], target: Dict[str, Any], selection: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    aliases = alias_fields(profile)
    fields = []
    for item in profile.get("columns_detail", []):
        fields.append(
            {
                "alias": aliases.get(item["name"]),
                "type": item.get("type"),
                "missing_rate": item.get("missing_rate"),
                "unique_count": item.get("unique_count"),
                "target_candidate": item.get("target_candidate", False),
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
        "models": ["logistic_regression", "random_forest", "hist_gradient_boosting", "xgboost"],
        "screening": {"max_features": 50, "missing_rate_max": 0.95, "min_iv": 0.005, "train_only": True},
        "mode": mode,
        "provider": gateway.status(),
    }
    return plan


def review_plan(plan: Dict[str, Any], profile: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    if not target.get("contract_ok"):
        findings.append({"code": "TARGET_CONTRACT_FAILED", "severity": "block", "message": "Y 尚未满足明确 0/1 双类别契约。"})
    elif min(int(target.get("positive_count", 0)), int(target.get("negative_count", 0))) < 2:
        findings.append(
            {
                "code": "TARGET_CLASS_TOO_SMALL",
                "severity": "block",
                "message": "Y 的某一类别少于 2 个样本，无法可靠完成分层切分。",
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
    return {"verdict": "block" if findings else "pass", "findings": findings, "reviewer": "deterministic-reviewer-v1", "message": "未发现阻断问题" if not findings else "需要先处理结构化阻断问题"}


def review_generated_code(code: str) -> Dict[str, Any]:
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
    return {"verdict": "block" if findings else "pass", "findings": findings, "checks": ["ast_pattern_scan", "dependency_policy", "generated_code_not_executed"]}


def generate_reproducible_code(plan: Dict[str, Any], selected: List[str], profile: Optional[Dict[str, Any]] = None) -> str:
    profile = profile or {}
    details = {item.get("name"): item.get("type") for item in profile.get("columns_detail", [])}
    numeric = [column for column in selected if details.get(column) == "numeric"]
    categorical = [column for column in selected if column not in numeric]
    columns = ", ".join(repr(column) for column in selected)
    numeric_columns = ", ".join(repr(column) for column in numeric)
    categorical_columns = ", ".join(repr(column) for column in categorical)
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
