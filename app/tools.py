"""Explicit local Tool Registry.

The registry is intentionally boring: it is a server-side allowlist, not an
LLM-discovered plugin system. Any future MCP adapter must map to these same
entries and cannot widen permissions at runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, FrozenSet, List


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    execution_class: str
    allowed_nodes: FrozenSet[str]
    permissions: FrozenSet[str]
    description: str


_SPECS = (
    ToolSpec("profile_dataset", "1.0.0", "in_process", frozenset({"profiling"}), frozenset({"read_dataset", "write_artifact"}), "建立字段、缺失和 Y 候选画像"),
    ToolSpec("quality_analysis", "1.0.0", "in_process", frozenset({"eda"}), frozenset({"read_dataset", "write_artifact"}), "执行本地数据质量与探索分析"),
    ToolSpec("build_cleaning_plan", "1.0.0", "in_process", frozenset({"cleaning"}), frozenset({"read_dataset", "write_artifact"}), "生成安全、可审计的清洗方案"),
    ToolSpec("apply_cleaning_plan", "1.0.0", "in_process", frozenset({"cleaning"}), frozenset({"read_dataset", "write_dataset_version", "write_artifact"}), "只执行已明确批准的清洗动作并生成新数据版本"),
    ToolSpec("select_features", "1.0.0", "in_process", frozenset({"screening"}), frozenset({"read_dataset", "write_artifact"}), "只在冻结训练分区执行变量筛选"),
    ToolSpec("train_candidate", "1.0.0", "sandboxed_process", frozenset({"training"}), frozenset({"read_dataset", "write_model", "write_artifact"}), "在受控子进程训练候选模型"),
    ToolSpec("review_generated_code", "1.0.0", "in_process", frozenset({"reporting"}), frozenset({"read_artifact", "provider_safe_evidence"}), "审核生成代码的静态安全边界"),
    ToolSpec("render_report", "1.0.0", "in_process", frozenset({"reporting"}), frozenset({"read_artifact", "write_artifact"}), "从同一次 Run 产物渲染报告"),
    ToolSpec("segment_analysis", "1.0.0", "in_process", frozenset({"analysis"}), frozenset({"read_dataset", "aggregate_output"}), "执行有界的 1—4 维聚合分析"),
)

REGISTRY: Dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def require_tool(name: str, node: str) -> ToolSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"TOOL_NOT_REGISTERED: {name}")
    if node not in spec.allowed_nodes:
        raise ValueError(f"TOOL_NODE_FORBIDDEN: {name} cannot run at {node}")
    return spec


def registry_manifest() -> List[Dict[str, object]]:
    return [asdict(spec) | {"allowed_nodes": sorted(spec.allowed_nodes), "permissions": sorted(spec.permissions)} for spec in _SPECS]
