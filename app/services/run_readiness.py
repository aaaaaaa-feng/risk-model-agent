"""Run preflight and comparable experiments using verified local evidence."""

from __future__ import annotations

from typing import Any

from app.core.config import SettingsStore
from app.domain.optimization import Objective
from app.governance.manifest import canonical_hash
from app.providers.gateway import ProviderGateway
from app.workers.profiling import target_summary
from app.workers.modeling import available_models


def preflight(
    ctx: Any,
    project_id: str,
    task_id: str,
    objective: dict | None = None,
    test_provider: bool = False,
) -> dict:
    task = ctx.catalog.require("target_tasks", task_id)
    if task["project_id"] != project_id:
        raise ValueError("CROSS_PROJECT_RUN_FORBIDDEN")
    config = Objective.model_validate(objective or {}).model_dump()
    frame = ctx.catalog.dataset_frame(task["dataset_version_id"])
    settings = SettingsStore(ctx.paths).load()
    blockers, warnings = [], []
    try:
        summary = target_summary(frame, task["target_column"])
        sample = {k: v for k, v in summary.items() if k not in {"normalized", "valid_mask"}}
        if (
            sample.get("valid_count", 0) < 30
            or min(sample.get("positive_count", 0), sample.get("negative_count", 0)) < 5
        ):
            blockers.append(
                {
                    "code": "INSUFFICIENT_TARGET_SAMPLES",
                    "action": "更换包含足够正负标签的样本或修正目标字段",
                }
            )
    except (ValueError, KeyError):
        sample = {}
        blockers.append({"code": "TARGET_INVALID", "action": "重新选择存在且有效的 0/1 标签字段"})
    gateway = ProviderGateway(settings=settings, paths=ctx.paths)
    connectivity = "not_tested"
    if settings.llm_enabled:
        if not gateway.configured:
            blockers.append(
                {
                    "code": "PROVIDER_CONFIGURATION_INCOMPLETE",
                    "action": "在模型设置中填写配置并测试连接，或明确关闭 LLM 使用本地策略模式",
                }
            )
        elif test_provider:
            result = gateway.connectivity_check()
            connectivity = "passed" if result.ok else "failed"
            if not result.ok:
                blockers.append(
                    {"code": "PROVIDER_CONNECTIVITY_FAILED", "action": "检查模型设置后重新测试连接"}
                )
        if settings.run_token_budget <= 0:
            blockers.append(
                {
                    "code": "RUN_TOKEN_BUDGET_REQUIRED",
                    "action": "在模型设置中配置有限的单次 Token 预算",
                }
            )
    else:
        warnings.append("本次为确定性本地策略，不是真实 LLM Agent 效果验证")
    if connectivity == "not_tested" and settings.llm_enabled:
        warnings.append("尚未测试模型连通性，配置完整不等于连接可用")
    requested = settings.default_models or []
    runnable = [m for m in requested if available_models().get(m)]
    if not runnable:
        blockers.append(
            {"code": "NO_AVAILABLE_MODELS", "action": "安装依赖或在模型设置中选择可用算法"}
        )
    if len(runnable) * 12 > config["max_candidate_fits"]:
        blockers.append(
            {
                "code": "INITIAL_FIT_BUDGET_INSUFFICIENT",
                "action": "减少初始候选算法或提高候选拟合预算",
            }
        )
    from app.services.pipeline import _first_candidate, _preferred_customer_key
    from app.workers.splitting import freeze_target_samples, split_dataset
    from app.workers.profiling import diagnose_frame

    profile = diagnose_frame(frame, task["target_column"])["profile"] if sample else {}
    time_column = _first_candidate(profile, "time_candidate")
    split_summary = {"status": "blocked"}
    if sample and not any(item["code"] == "INSUFFICIENT_TARGET_SAMPLES" for item in blockers):
        try:
            frozen, _ = freeze_target_samples(frame, task["target_column"])
            split = split_dataset(
                frozen,
                task["target_column"],
                method="time_holdout" if time_column else "random_stratified",
                time_column=time_column,
                customer_key=_preferred_customer_key(profile),
                oot_size=0.2 if time_column else 0,
            )
            split_summary = {k: v for k, v in split.items() if k != "indices"}
            split_summary["status"] = "validated_proposal_requires_confirmation"
        except ValueError as error:
            blockers.append(
                {
                    "code": str(error).split(":")[0],
                    "action": "检查时间／客户字段和各分区样本量，修正数据后重试",
                }
            )
    return {
        "schema_version": "risk-preflight/v1",
        "executable": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "sample": sample,
        "objective": config,
        "mode": "real_model" if settings.llm_enabled else "deterministic_product",
        "provider": {"configured": gateway.configured, "connectivity": connectivity},
        "resources": {
            "memory_budget_mb": settings.memory_budget_mb,
            "models": runnable,
            "run_token_budget": settings.run_token_budget,
        },
        "split": {"status": "requires_confirmation", "final_holdout": "locked_after_split"},
    }


def compare_runs(left: dict, right: dict, database: Any) -> dict:
    if left["project_id"] != right["project_id"]:
        raise ValueError("CROSS_PROJECT_COMPARISON_FORBIDDEN")
    states = [run.get("state") or {} for run in (left, right)]
    keys = ("target", "working_data_sha256", "split_hash", "score")
    reasons = []
    snapshots = [s.get("objective_snapshot") or {} for s in states]
    if not all(snap.get("working_data_sha256") for snap in snapshots):
        reasons.append("working_data_hash_missing")
    if not all(snapshots):
        reasons.append("objective_snapshot_missing")
    for key in keys:
        if snapshots[0].get(key) != snapshots[1].get(key):
            reasons.append(f"{key}_different")
    # Version IDs differ for equivalent cleaning outputs; compare content hashes from manifests.
    data_hashes = []
    for run in (left, right):
        manifests = database.list("run_manifests", {"run_id": run["id"]}, limit=1)
        payload = (manifests[0].get("payload") or {}) if manifests else {}
        data_hashes.append((payload.get("dataset") or {}).get("content_sha256"))
    if not all(data_hashes) or canonical_hash(data_hashes[0]) != canonical_hash(data_hashes[1]):
        reasons.append("dataset_different_or_unknown")
    score_objectives = [
        {
            k: v
            for k, v in (snap.get("objective") or {}).items()
            if k not in {"strategy", "max_optimizations"}
        }
        for snap in snapshots
    ]
    if score_objectives[0] != score_objectives[1]:
        reasons.append("objective_or_budget_different")
    summaries = []
    for run, state in zip((left, right), states):
        requests = database.list_all("provider_requests", {"run_id": run["id"]})
        rounds = state.get("optimization_rounds") or []
        best = rounds[state["best_round_id"]] if rounds and "best_round_id" in state else {}
        summaries.append(
            {
                "run_id": run["id"],
                "status": run["status"],
                "plan": best.get("plan"),
                "metrics": (state.get("model_result") or {}).get("champion_metrics"),
                "stop_reason": state.get("optimization_stop_reason"),
                "goal_status": state.get("goal_status"),
                "candidate_fits": state.get("candidate_fits_used"),
                "reserved_fits": state.get("candidate_fits_reserved"),
                "duration_seconds": sum(r.get("duration_seconds", 0) for r in rounds),
                "total_compute_seconds": state.get("compute_seconds_used"),
                "model_calls": len(requests),
                "cost_usd": None,
                "total_tokens": sum(
                    (r.get("usage") or {}).get("total_tokens", 0) or 0 for r in requests
                ),
            }
        )
    return {
        "schema_version": "risk-comparison/v1",
        "directly_comparable": not reasons,
        "reasons": reasons,
        "runs": summaries,
        "note": "Test 为开发选择集；OOT 仅用于最终验证。成本未知显示 null，不按轮数推断资源相等。",
    }
