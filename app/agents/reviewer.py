from __future__ import annotations

import json
from typing import Any

from app.providers.gateway import ProviderGateway

from .codegen import review_generated_code
from .prompts import REVIEWER_PROMPT


APPROVED_REVIEW_STATUSES = {
    "deterministic_pass",
    "llm_reviewer_pass",
    "fallback_pass",
    "conditional_pass",
}


def review_is_approved(value: dict[str, Any] | str | None) -> bool:
    status = value.get("status") if isinstance(value, dict) else value
    return status in APPROVED_REVIEW_STATUSES


def review_blocks_progress(value: dict[str, Any] | None) -> bool:
    if not value:
        return False
    if value.get("status") in {"block", "blocked"}:
        return True
    return any(item.get("severity") == "blocking" for item in (value.get("issues") or []))


def review_requires_revision(value: dict[str, Any] | None) -> bool:
    return bool(value and value.get("status") == "revise")


class IndependentReviewer:
    def __init__(self, gateway: ProviderGateway | None = None):
        self.gateway = gateway or ProviderGateway()

    def review_plan(
        self,
        plan: dict[str, Any],
        diagnostics: dict[str, Any],
        screening: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for item in diagnostics.get("issues", []):
            if item.get("severity") == "blocking":
                issues.append(item)
        split_method = plan.get("split", {}).get("method")
        if split_method == "time_holdout" and not plan.get("split", {}).get("time_column"):
            issues.append(
                {
                    "code": "TIME_COLUMN_REQUIRED",
                    "severity": "blocking",
                    "message": "时间外推必须指定时间字段。",
                }
            )
        if screening is not None and not screening.get("included"):
            issues.append(
                {
                    "code": "NO_FEATURES_AFTER_SCREENING",
                    "severity": "blocking",
                    "message": "筛选后没有可入模变量。",
                }
            )
        return self._record(
            "plan",
            issues,
            {
                "train_only": True,
                "split_method": split_method,
                "oot_locked": split_method == "time_holdout",
            },
        )

    def review_code(self, source: str) -> dict[str, Any]:
        result = review_generated_code(source)
        return self._record("code", result["findings"], result.get("checks", {}))

    def review_execution(self, model_result: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        failed_candidates: list[dict[str, Any]] = []
        if not model_result.get("champion"):
            issues.append(
                {"code": "CHAMPION_MISSING", "severity": "blocking", "message": "没有冠军模型。"}
            )
        if model_result.get("oot_used_for_selection") is not False:
            issues.append(
                {
                    "code": "OOT_SELECTION_LEAK",
                    "severity": "blocking",
                    "message": "OOT 被用于模型选择。",
                }
            )
        for item in model_result.get("candidates", []):
            if item.get("status") != "trained":
                failed_candidates.append(
                    {
                        "candidate": item.get("candidate"),
                        "error_code": item.get("error_code"),
                    }
                )
                continue
            if item.get("fit_scope") != "train_cv_only" or item.get("selection_scope") != "test":
                issues.append(
                    {
                        "code": "FIT_SCOPE_INVALID",
                        "severity": "blocking",
                        "message": f"{item.get('candidate')} 的拟合/选择范围不合规。",
                    }
                )
            metrics = item.get("test_metrics") or {}
            if metrics.get("roc_auc") is None or metrics.get("ks") is None:
                issues.append(
                    {
                        "code": "MODEL_METRICS_MISSING",
                        "severity": "blocking",
                        "message": f"{item.get('candidate')} 缺少 AUC/KS。",
                    }
                )
        return self._record(
            "execution",
            issues,
            {
                "candidate_count": len(model_result.get("candidates", [])),
                "failed_candidates": failed_candidates,
                "failed_candidates_isolated": True,
            },
        )

    def review_report(self, report: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        champion = report.get("champion") or {}
        summary = report.get("executive_summary") or {}
        if champion.get("candidate") != summary.get("champion"):
            issues.append(
                {
                    "code": "REPORT_CHAMPION_MISMATCH",
                    "severity": "blocking",
                    "message": "摘要与专业详情的 Champion 不一致。",
                }
            )
        if (champion.get("test_metrics") or {}).get("roc_auc") != summary.get("test_auc"):
            issues.append(
                {
                    "code": "REPORT_METRIC_MISMATCH",
                    "severity": "blocking",
                    "message": "报告中的 Test AUC 不一致。",
                }
            )
        if report.get("governance", {}).get("oot_used_for_selection") is not False:
            issues.append(
                {
                    "code": "REPORT_OOT_SCOPE_INVALID",
                    "severity": "blocking",
                    "message": "报告未声明 OOT 仅用于最终评估。",
                }
            )
        absolute = bool(summary.get("absolute_ordering"))
        expected_verdict = "pass" if absolute else "conditional"
        if summary.get("quality_verdict") != expected_verdict:
            issues.append(
                {
                    "code": "REPORT_QUALITY_VERDICT_MISMATCH",
                    "severity": "blocking",
                    "message": "报告质检结论与 Test 排序性不一致。",
                }
            )
        return self._record(
            "report",
            issues,
            {
                "schema_version": report.get("schema_version"),
                "absolute_ordering_checked": True,
                "quality_verdict": summary.get("quality_verdict"),
            },
        )

    def llm_review(self, scope: str, safe_evidence: dict[str, Any]) -> dict[str, Any]:
        if not self.gateway.enabled:
            return {
                "scope": scope,
                "status": "fallback_pass",
                "issues": [],
                "evidence": {"provider": "disabled", "deterministic_review_retained": True},
            }
        payload, result = self.gateway.complete_json(
            REVIEWER_PROMPT.content,
            {"scope": scope, "review_material": safe_evidence, "response_schema": "risk-review/v1"},
            model=self.gateway.settings.reviewer_model or self.gateway.settings.model,
            purpose=f"reviewer_{scope}",
        )
        if not payload:
            return {
                "scope": scope,
                "status": "fallback_pass",
                "issues": [],
                "evidence": {
                    "provider_error": result.error_code,
                    "deterministic_review_retained": True,
                },
            }
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        source_status = payload.get("status")
        status = {
            "pass": "llm_reviewer_pass",
            "revise": "revise",
            "block": "blocked",
        }.get(source_status, "revise")
        return {
            "scope": scope,
            "status": status,
            "issues": issues,
            "evidence": {
                "provider": self.gateway.settings.provider,
                "model": result.model,
                "payload_hash": result.payload_hash,
            },
        }

    @staticmethod
    def combine(scope: str, deterministic: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
        issues = [*(deterministic.get("issues") or []), *(llm.get("issues") or [])]
        if (
            any(item.get("severity") == "blocking" for item in issues)
            or deterministic.get("status") in {"block", "blocked"}
            or llm.get("status") in {"block", "blocked"}
        ):
            status = "blocked"
        elif issues or llm.get("status") == "revise":
            status = "revise" if llm.get("status") == "revise" else "conditional_pass"
        elif llm.get("status") == "fallback_pass":
            status = "fallback_pass"
        elif llm.get("status") == "llm_reviewer_pass":
            status = "llm_reviewer_pass"
        else:
            status = "deterministic_pass"
        return {
            "scope": scope,
            "status": status,
            "issues": issues,
            "evidence": {
                "deterministic": deterministic.get("evidence", {}),
                "llm": llm.get("evidence", {}),
                "independent_context": True,
                "deterministic_status": deterministic.get("status"),
                "llm_status": llm.get("status"),
            },
        }

    @staticmethod
    def _record(
        scope: str, issues: list[dict[str, Any]], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        blocking = any(item.get("severity") == "blocking" for item in issues)
        return {
            "scope": scope,
            "status": (
                "blocked" if blocking else ("conditional_pass" if issues else "deterministic_pass")
            ),
            "issues": issues,
            "evidence": evidence,
        }


def review_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)
