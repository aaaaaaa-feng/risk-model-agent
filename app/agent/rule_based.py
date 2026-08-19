"""Evidence-bound, read-only guidance for the modeling workflow."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _section(kind: str, text: str) -> Dict[str, str]:
    return {"kind": kind, "text": text}


def _metric(metrics: Dict[str, Any], key: str) -> Optional[float]:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def build_agent_response(project: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Return an answer using stored artifacts only.

    The assistant is deliberately read-only. Suggested actions must still be
    triggered by the user through the corresponding API and state gate.
    """

    state = project.get("status", "unknown")
    profile = project.get("profile") or {}
    plan = project.get("plan") or {}
    latest_run = project.get("latest_run") or {}
    result = latest_run.get("result") or {}
    normalized = (message or "").strip().lower()
    sections: List[Dict[str, str]] = []
    suggested_action: Optional[str] = None

    wants_result = any(word in normalized for word in ("结果", "auc", "ks", "模型", "表现"))
    wants_risk = any(word in normalized for word in ("风险", "泄漏", "问题", "警告", "数据"))
    wants_help = any(word in normalized for word in ("帮助", "怎么", "是什么", "能力"))

    if wants_help:
        sections.append(
            _section(
                "fact",
                "我是离线规则化建模助手：读取本地画像、方案和训练结果，给出流程提示与证据化解读。",
            )
        )
        sections.append(
            _section(
                "risk",
                "我不会替你确认标签含义、静默批准方案，也不会把离线指标描述成生产效果。",
            )
        )

    if wants_result and result:
        champion = result.get("champion", {})
        metrics = result.get("holdout_metrics", {})
        model_name = champion.get("display_name") or champion.get("name") or "候选模型"
        auc = _metric(metrics, "roc_auc")
        ks = _metric(metrics, "ks")
        if auc is not None and ks is not None:
            sections.append(
                _section(
                    "fact",
                    f"本次留出集冠军是 {model_name}，ROC-AUC={auc:.4f}，KS={ks:.4f}。这些数字直接读取自本地结果文件。",
                )
            )
        else:
            sections.append(_section("fact", f"本次冠军是 {model_name}；部分指标不可计算。"))
        sections.append(
            _section(
                "risk",
                "这是指定数据与切分下的一次离线实验，不证明未来表现、业务收益、公平性或生产稳定性。",
            )
        )
        sections.append(
            _section(
                "suggestion",
                "下一轮可优先补充独立时间外样本，并核验标签窗口、实体跨集重复与字段可用时间。",
            )
        )

    if wants_risk:
        warnings = plan.get("warnings") or profile.get("warnings") or []
        blockers = plan.get("blocking_issues") or []
        sections.append(
            _section(
                "fact",
                f"当前方案记录 {len(blockers)} 个阻断项、{len(warnings)} 个风险提示。泄漏检测属于启发式筛查。",
            )
        )
        if blockers:
            first = blockers[0]
            text = first.get("message", str(first)) if isinstance(first, dict) else str(first)
            sections.append(_section("risk", f"首个阻断项：{text}"))
        elif warnings:
            first = warnings[0]
            text = first.get("message", str(first)) if isinstance(first, dict) else str(first)
            sections.append(_section("risk", f"首个提示：{text}"))
        else:
            sections.append(
                _section(
                    "risk",
                    "没有命中当前规则不等于不存在泄漏；仍需人工确认字段血缘和业务发生时间。",
                )
            )

    if not sections:
        if state == "profiled":
            rows = profile.get("row_count", project.get("dataset", {}).get("rows", "未知"))
            columns = profile.get("column_count", project.get("dataset", {}).get("columns", "未知"))
            sections.append(_section("fact", f"数据体检已完成：{rows} 行、{columns} 列。"))
            sections.append(
                _section("suggestion", "请明确目标字段、坏样本取值和可选时间字段，生成方案草稿。")
            )
            suggested_action = "create_plan"
        elif state == "awaiting_approval":
            feature_count = len((plan.get("features") or {}).get("included_columns") or [])
            sections.append(
                _section(
                    "fact",
                    f"方案 v{plan.get('version', 1)} 已生成，计划纳入 {feature_count} 个字段。",
                )
            )
            if plan.get("blocking_issues"):
                sections.append(_section("risk", "方案仍有阻断项，不能批准训练。"))
                suggested_action = "review_blockers"
            else:
                sections.append(
                    _section(
                        "suggestion", "请逐项核对标签、样本范围、泄漏提醒和离线验证边界后再批准。"
                    )
                )
                suggested_action = "approve_plan"
        elif state == "approved":
            sections.append(_section("fact", "当前批准记录已绑定数据集哈希与方案哈希。"))
            sections.append(_section("suggestion", "可以启动本地确定性训练；Agent 不会自动触发。"))
            suggested_action = "train"
        elif state == "training":
            sections.append(_section("fact", "本地训练正在执行。"))
            sections.append(_section("suggestion", "请等待本次不可变运行完成，不要重复提交训练。"))
        elif state == "completed" and result:
            sections.append(_section("fact", "训练已完成，结果区与报告均来自本地运行产物。"))
            sections.append(_section("suggestion", "可以询问“结果怎么样”或“有哪些风险”。"))
            suggested_action = "review_result"
        elif state == "failed":
            sections.append(_section("risk", "最近一次训练失败；失败状态不会沿用旧运行指标。"))
            sections.append(
                _section("suggestion", "先查看审计事件中的错误原因，再决定是否按同一批准方案重试。")
            )
        else:
            sections.append(_section("suggestion", "先创建项目并上传一份符合数据契约的 CSV。"))
            suggested_action = "upload_dataset"

    return {
        "mode": "deterministic_offline_assistant",
        "mode_label": "规则化助手 · 本地只读",
        "state": state,
        "sections": sections,
        "suggested_action": suggested_action,
        "boundary": "Agent 只解释本地产物，不计算指标、不批准方案、不作授信决定。",
    }
