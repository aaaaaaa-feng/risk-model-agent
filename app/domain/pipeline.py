"""建模阶段、工具与编排节点共用的唯一业务契约。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PipelineStepSpec:
    graph_node: str
    tool_name: str
    stage: str
    agent: str
    description: str
    summary: str
    handler: str


PIPELINE_STEPS = (
    PipelineStepSpec(
        "prepare_target",
        "prepare_target",
        "target_confirmation",
        "main_agent",
        "冻结 0/1 有效样本并提出 Y 证据",
        "已检查 Y 与有效样本",
        "prepare_target",
    ),
    PipelineStepSpec(
        "diagnose",
        "diagnose_data",
        "data_diagnosis",
        "main_agent",
        "本地数据质量、粒度和泄漏诊断",
        "已完成建模前诊断",
        "diagnose",
    ),
    PipelineStepSpec(
        "clean",
        "apply_cleaning",
        "cleaning",
        "local_worker",
        "按已确认动作生成新的清洗数据版本",
        "已生成清洗数据版本",
        "clean",
    ),
    PipelineStepSpec(
        "propose_split",
        "propose_split",
        "split",
        "main_agent",
        "推荐 Train/Test/OOT 与客户隔离方案",
        "已提出样本切分方案",
        "propose_split",
    ),
    PipelineStepSpec(
        "execute_split",
        "execute_split",
        "split",
        "local_worker",
        "执行并校验样本切分",
        "已完成 Train/Test/OOT 切分",
        "execute_split",
    ),
    PipelineStepSpec(
        "screen",
        "screen_features",
        "screening",
        "local_worker",
        "Train-only 缺失率、IV、相关性与泄漏筛选",
        "已完成 Train-only 变量筛选",
        "screen",
    ),
    PipelineStepSpec(
        "finalize_screen",
        "finalize_screening",
        "screening",
        "main_agent",
        "应用有理由的人工变量恢复",
        "已冻结最终入模变量",
        "finalize_screening",
    ),
    PipelineStepSpec(
        "bin_features",
        "fit_binning",
        "binning",
        "local_worker",
        "Train-only 自动单调分箱",
        "已完成自动单调分箱",
        "bin_features",
    ),
    PipelineStepSpec(
        "finalize_binning",
        "finalize_binning",
        "binning",
        "main_agent",
        "验证人工分箱并使下游产物失效",
        "已冻结分箱版本",
        "finalize_binning",
    ),
    PipelineStepSpec(
        "propose_models",
        "propose_models",
        "model_plan",
        "main_agent",
        "按资源与 Provider 建议候选模型",
        "已提出候选模型与评分方案",
        "propose_models",
    ),
    PipelineStepSpec(
        "finalize_models",
        "finalize_model_plan",
        "model_plan",
        "main_agent",
        "应用用户确认的模型与评分参数",
        "已冻结建模方案",
        "finalize_model_plan",
    ),
    PipelineStepSpec(
        "code_review",
        "generate_and_review_code",
        "code_review",
        "reviewer_agent",
        "主 Agent 生成 Notebook，独立 Reviewer 闭环审核",
        "代码已完成独立质检",
        "generate_and_review_code",
    ),
    PipelineStepSpec(
        "train_review",
        "train_and_review",
        "training",
        "reviewer_agent",
        "本地训练、校准、选型与独立执行质检",
        "训练、校准与执行质检已完成",
        "train_and_review",
    ),
    PipelineStepSpec(
        "report_review",
        "build_and_review_report",
        "reporting",
        "reviewer_agent",
        "生成唯一结构化报告并独立质检",
        "结构化报告已完成独立质检",
        "build_and_review_report",
    ),
    PipelineStepSpec(
        "write_artifacts",
        "write_artifacts",
        "reporting",
        "local_worker",
        "导出 Web/Excel/HTML/模型包与评分入口",
        "报告、模型包与评分入口已生成",
        "write_artifacts",
    ),
)


def partition_model_proposals(
    values: Iterable[object], availability: Mapping[str, bool]
) -> tuple[list[str], list[str]]:
    """把 LLM 候选限制为本地注册表，并为所有拒绝项保留安全证据。"""

    accepted: list[str] = []
    rejected: list[str] = []
    for value in values:
        if not isinstance(value, str):
            rejected.append("invalid_model_identifier")
            continue
        if availability.get(value, False):
            accepted.append(value)
            continue
        rejected.append(
            value if re.fullmatch(r"[a-z0-9_-]{1,64}", value) else "invalid_model_identifier"
        )
    return list(dict.fromkeys(accepted)), list(dict.fromkeys(rejected))
