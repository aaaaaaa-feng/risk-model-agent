"""Run 编排使用的状态与节点契约。"""

from __future__ import annotations

from typing import TypedDict

from app.domain.pipeline import PIPELINE_STEPS


class RunState(TypedDict, total=False):
    run_id: str
    project_id: str
    target_task_id: str
    mode: str
    halted: bool
    target: str
    target_evidence: dict
    target_gate: dict
    target_review: dict
    target_decision: dict
    profile: dict
    diagnostics: dict
    cleaning_plan: dict
    cleaning_result: dict
    data_gate: dict
    data_review: dict
    data_decision: dict
    working_dataset_version_id: str
    split_plan: dict
    split: dict
    split_gate: dict
    split_review: dict
    split_decision: dict
    screening: dict
    screening_gate: dict
    screening_review: dict
    screening_decision: dict
    binning: dict
    binning_gate: dict
    binning_review: dict
    binning_decision: dict
    model_plan: dict
    model_gate: dict
    model_plan_review: dict
    model_decision: dict
    model_result: dict
    report: dict
    report_review: dict
    execution_review: dict
    code_review: dict
    generated_code_path: str
    field_aliases: dict[str, str]
    effective_models: list[str]
    model_version_id: str
    package_manifest: dict
    worker_bundle_manifest_sha256: str
    artifact_ids: list[str]
    trace_id: str
    root_span_id: str


# 兼容既有导入名，但数据只来源于 domain 中的一份稳定契约。
TOOL_NODES = PIPELINE_STEPS

GATES = {
    "confirm_target": ("target_confirmation", "target_gate", "target_decision"),
    "confirm_data": ("data_diagnosis", "data_gate", "data_decision"),
    "confirm_split": ("split", "split_gate", "split_decision"),
    "confirm_screening": ("screening", "screening_gate", "screening_decision"),
    "confirm_binning": ("binning", "binning_gate", "binning_decision"),
    "confirm_models": ("model_plan", "model_gate", "model_decision"),
}


def node_position(node: str) -> int:
    for index, item in enumerate(TOOL_NODES):
        if item.graph_node == node:
            return index
    return 0
