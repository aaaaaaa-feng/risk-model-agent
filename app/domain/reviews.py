"""Reviewer 终态规则。

这些判断同时被 Agent 编排、确定性报告和应用服务使用，因此属于领域规则，
不能放在某个具体 Agent 实现里形成反向依赖。
"""

from __future__ import annotations

from typing import Any


APPROVED_REVIEW_STATUSES = frozenset(
    {
        "deterministic_pass",
        "llm_reviewer_pass",
        "fallback_pass",
        "conditional_pass",
    }
)


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
