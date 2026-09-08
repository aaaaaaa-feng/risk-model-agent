from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.agents.evidence import build_safe_evidence
from app.agents.prompts import MODEL_PLAN_PROMPT
from app.agents.reviewer import IndependentReviewer
from app.core.config import SettingsStore
from app.core.database import Database, new_id, now_iso
from app.core.paths import AppPaths, get_paths
from app.core.security import sha256_bytes, sha256_file
from app.domain.pipeline import PIPELINE_STEPS, partition_model_proposals
from app.domain.reviews import review_blocks_progress, review_is_approved
from app.governance.tracing import TraceService
from app.providers.gateway import ProviderGateway
from app.tooling.registry import ToolRegistry
from app.workers.binning import apply_manual_binning, fit_binning
from app.workers.io import plan_resources
from app.workers.modeling import ModelBundle, available_models, recommend_models, train_candidates
from app.workers.package_runtime import (
    SKOPS_POLICY_VERSION,
    inspect_skops_types,
    load_skops_model,
)
from app.workers.profiling import (
    apply_cleaning,
    cleaning_plan,
    diagnose_frame,
    target_summary,
)
from app.workers.screening import restore_features, screen_features
from app.workers.splitting import freeze_target_samples, split_dataset

from .artifacts import ArtifactService
from .catalog import CatalogService
from .pipeline_contracts import RunToolInput


class RunPipeline:
    """Deterministic local stage implementation invoked by LangGraph nodes."""

    def __init__(
        self,
        database: Database | None = None,
        paths: AppPaths | None = None,
        catalog: CatalogService | None = None,
        artifacts: ArtifactService | None = None,
        provider_client_factory: Any = None,
        provider_api_key: str | None = None,
        reviewer_factory: Callable[[ProviderGateway], IndependentReviewer] | None = None,
    ):
        self.paths = paths or get_paths()
        self.database = database or Database(paths=self.paths)
        self.catalog = catalog or CatalogService(self.database, self.paths)
        self.artifacts = artifacts or ArtifactService(self.database, self.paths, self.catalog)
        self.provider_client_factory = provider_client_factory
        self.provider_api_key = provider_api_key
        self.reviewer_factory = reviewer_factory or IndependentReviewer
        self.traces = TraceService(self.database)
        self._active_parent_span_id: str | None = None
        self._bundles: dict[str, dict[str, ModelBundle]] = {}
        self.registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        for spec in PIPELINE_STEPS:
            handler = getattr(self, spec.handler)
            self.registry.register(
                spec.tool_name,
                spec.stage,
                spec.description,
                RunToolInput,
                lambda value, target=handler: target(value.run_id, value.state),
            )

    def invoke(self, name: str, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        previous = self._active_parent_span_id
        self._active_parent_span_id = state.get("_trace_parent_span_id")
        try:
            return self.registry.invoke(name, {"run_id": run_id, "state": state})
        finally:
            self._active_parent_span_id = previous

    def prepare_target(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        task, dataset, frame = self._context(run_id)
        summary = target_summary(frame, task["target_column"])
        profile = dataset.get("profile") or diagnose_frame(frame, task["target_column"])["profile"]
        evidence = {
            key: value for key, value in summary.items() if key not in {"valid_mask", "normalized"}
        }
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {"split": {"method": "random_stratified"}}, {"issues": evidence.get("issues", [])}
        )
        safe, _ = build_safe_evidence(profile, evidence)
        review = reviewer.combine("target", deterministic, reviewer.llm_review("target", safe))
        self._record_review(run_id, review)
        return {
            "target": task["target_column"],
            "target_evidence": evidence,
            "profile": profile,
            "working_dataset_version_id": dataset["id"],
            "target_gate": {
                "title": "确认 Y 与有效样本",
                "summary": {"target": evidence, "review": review},
                "editable": ["positive_label", "negative_label", "excluded_labels"],
            },
            "target_review": review,
        }

    def diagnose(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame = self._working_frame(state)
        diagnostics = diagnose_frame(
            frame,
            state["target"],
            _first_candidate(state["profile"], "time_candidate"),
        )
        proposed_cleaning = cleaning_plan(
            frame,
            state["target"],
            _first_candidate(state["profile"], "time_candidate"),
        )
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {
                "split": {
                    "method": "time_holdout"
                    if _first_candidate(state["profile"], "time_candidate")
                    else "random_stratified",
                    "time_column": _first_candidate(state["profile"], "time_candidate"),
                }
            },
            diagnostics,
        )
        safe, aliases = build_safe_evidence(diagnostics["profile"], diagnostics["target"])
        llm = reviewer.llm_review("data_diagnosis", safe)
        review = reviewer.combine("data_diagnosis", deterministic, llm)
        self._record_review(run_id, review)
        return {
            "diagnostics": diagnostics,
            "cleaning_plan": proposed_cleaning,
            "field_aliases": aliases,
            "data_review": review,
            "data_gate": {
                "title": "确认数据诊断与清洗",
                "summary": {
                    "issues": diagnostics["issues"],
                    "actions": proposed_cleaning["actions"],
                    "review": review,
                },
                "editable": ["accepted_action_ids"],
            },
        }

    def clean(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame = self._working_frame(state)
        actions = list(state["cleaning_plan"].get("actions", []))
        decision = state.get("data_decision") or {}
        accepted = (decision.get("edits") or {}).get("accepted_action_ids")
        if accepted is not None:
            accepted_set = set(accepted)
            actions = [item for item in actions if item.get("id") in accepted_set]
        if not actions:
            return {
                "cleaning_result": {
                    "applied": [],
                    "rows": len(frame),
                    "columns": len(frame.columns),
                }
            }
        cleaned, evidence = apply_cleaning(frame, actions)
        run = self.catalog.require("runs", run_id)
        version = self.catalog.create_dataset_version(
            run["project_id"],
            cleaned,
            f"Run {run_id[-6:]} · 清洗版本",
            [state["working_dataset_version_id"]],
            {"kind": "cleaning", "run_id": run_id, "actions": evidence["applied"]},
        )
        return {
            "working_dataset_version_id": version["id"],
            "cleaning_result": evidence,
            "profile": version["profile"],
        }

    def propose_split(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        time_column = _first_candidate(state["profile"], "time_candidate")
        customer_key = _preferred_customer_key(state["profile"])
        plan = {
            "method": "time_holdout" if time_column else "random_stratified",
            "time_column": time_column,
            "customer_key": customer_key,
            "test_size": 0.20,
            "oot_size": 0.20 if time_column else 0,
            "random_state": 42,
            "customer_isolation": bool(customer_key),
        }
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {"split": plan}, state["diagnostics"], state.get("screening")
        )
        safe, _ = build_safe_evidence(state["profile"], state["target_evidence"])
        review = reviewer.combine("split", deterministic, reviewer.llm_review("split", safe))
        self._record_review(run_id, review)
        return {
            "split_plan": plan,
            "split_review": review,
            "split_gate": {
                "title": "确认 Train / Test / OOT 切分",
                "summary": {"plan": plan, "review": review},
                "editable": ["method", "time_column", "customer_key", "test_size", "oot_size"],
            },
        }

    def execute_split(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        plan = {**state["split_plan"], **((state.get("split_decision") or {}).get("edits") or {})}
        split = split_dataset(
            frame,
            state["target"],
            method=plan["method"],
            time_column=plan.get("time_column"),
            customer_key=plan.get("customer_key"),
            test_size=float(plan.get("test_size", 0.2)),
            oot_size=float(plan.get("oot_size", 0.2)),
            random_state=int(plan.get("random_state", 42)),
        )
        run = self.catalog.require("runs", run_id)
        self.database.update(
            "target_tasks", run["target_task_id"], {"split_json": split, "updated_at": now_iso()}
        )
        return {"split": split, "split_plan": plan}

    def screen(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        train = frame.iloc[state["split"]["indices"]["train"]]
        screening = screen_features(
            train,
            state["target"],
            protected_targets=state["profile"].get("binary_candidates", []),
        )
        run = self.catalog.require("runs", run_id)
        self.database.update(
            "target_tasks",
            run["target_task_id"],
            {"screening_json": screening, "updated_at": now_iso()},
        )
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {"split": state["split_plan"]}, state["diagnostics"], screening
        )
        safe, _ = build_safe_evidence(state["profile"], state["target_evidence"], screening)
        review = reviewer.combine(
            "screening", deterministic, reviewer.llm_review("screening", safe)
        )
        self._record_review(run_id, review)
        return {
            "screening": screening,
            "screening_review": review,
            "screening_gate": {
                "title": "确认变量筛选",
                "summary": {
                    "thresholds": screening["thresholds"],
                    "included": screening["included"],
                    "excluded": screening["excluded"],
                    "review": review,
                },
                "editable": ["restore_features"],
                "non_recoverable": [
                    "PII",
                    "LEAKAGE",
                    "TARGET",
                    "OTHER_TARGET",
                    "IDENTIFIER",
                    "CONSTANT",
                ],
            },
        }

    def finalize_screening(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        requests = ((state.get("screening_decision") or {}).get("edits") or {}).get(
            "restore_features", []
        )
        screening = state["screening"]
        if requests:
            screening = restore_features(screening, requests)
        if not screening.get("included"):
            raise ValueError("NO_FEATURES_AFTER_SCREENING")
        run = self.catalog.require("runs", run_id)
        self.database.update(
            "target_tasks",
            run["target_task_id"],
            {"screening_json": screening, "updated_at": now_iso()},
        )
        return {"screening": screening}

    def bin_features(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        train = frame.iloc[state["split"]["indices"]["train"]]
        binning = fit_binning(train, state["target"], state["screening"]["included"])
        non_monotonic = [
            name for name, spec in binning["specs"].items() if not spec.get("monotonic")
        ]
        reviewer = self._reviewer(run_id)
        deterministic = {
            "scope": "binning",
            "status": "conditional_pass" if non_monotonic else "deterministic_pass",
            "issues": [
                {
                    "code": "NON_MONOTONIC_BINNING",
                    "severity": "warning",
                    "message": "存在未达到绝对单调的变量，可在确认节点人工调整。",
                    "columns": non_monotonic,
                }
            ]
            if non_monotonic
            else [],
            "evidence": {"fit_scope": "train_only", "binning_version": binning["version"]},
        }
        safe, _ = build_safe_evidence(
            state["profile"], state["target_evidence"], state["screening"]
        )
        review = reviewer.combine("binning", deterministic, reviewer.llm_review("binning", safe))
        self._record_review(run_id, review)
        return {
            "binning": binning,
            "binning_review": review,
            "binning_gate": {
                "title": "确认自动分箱",
                "summary": {
                    "version": binning["version"],
                    "non_monotonic": non_monotonic,
                    "specs": binning["specs"],
                    "review": review,
                },
                "editable": ["manual_specs"],
            },
        }

    def finalize_binning(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        manual = ((state.get("binning_decision") or {}).get("edits") or {}).get("manual_specs", {})
        binning = state["binning"]
        if manual:
            frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
            train = frame.iloc[state["split"]["indices"]["train"]]
            for column, spec in manual.items():
                binning = apply_manual_binning(binning, train, state["target"], column, spec)
        run = self.catalog.require("runs", run_id)
        self.database.update(
            "target_tasks",
            run["target_task_id"],
            {"binning_json": binning, "updated_at": now_iso()},
        )
        return {"binning": binning}

    def propose_models(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        settings = SettingsStore(self.paths).load()
        resource = plan_resources(len(frame), len(frame.columns), settings.memory_budget_mb)
        availability = available_models()
        requested_defaults = list(dict.fromkeys(settings.default_models or []))
        configured = [name for name in requested_defaults if availability.get(name, False)]
        unavailable_defaults = [
            name for name in requested_defaults if not availability.get(name, False)
        ]
        models = list(dict.fromkeys(configured or recommend_models(resource)))
        plan = {
            "objective": state.get("requested_objective", {}),
            "models": models,
            "unavailable_models": unavailable_defaults,
            "search_budget": 0,
            "search_budget_max": 12,
            "resource_plan": resource.as_dict(),
            "score": {
                "minimum": 300,
                "maximum": 900,
                "base_score": 600,
                "base_odds": 20,
                "pdo": 50,
            },
            "selection_reference": {
                "auc": 0.55,
                "ks": 0.15,
                "hard_threshold": False,
                "absolute_ordering_required": True,
            },
        }
        safe, aliases = build_safe_evidence(
            state["profile"], state["target_evidence"], state["screening"]
        )
        gateway = self._gateway(run_id)
        if gateway.enabled:
            payload, result = gateway.complete_json(
                MODEL_PLAN_PROMPT.content,
                {"planning_material": safe, "deterministic_recommendation": models},
                purpose="main_agent_model_plan",
            )
            current_availability = available_models()
            valid, rejected = partition_model_proposals(
                payload.get("models", []) if payload else [], current_availability
            )
            plan["llm_rejected_models"] = rejected
            if valid:
                plan["models"] = list(dict.fromkeys(["dummy", *valid]))
                plan["source"] = "llm_reviewed_and_locally_validated"
                plan["provider_evidence"] = {
                    "model": result.model,
                    "payload_hash": result.payload_hash,
                }
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {"split": state["split_plan"], "models": plan["models"]},
            state["diagnostics"],
            state["screening"],
        )
        llm = reviewer.llm_review("model_plan", safe)
        review = reviewer.combine("model_plan", deterministic, llm)
        self._record_review(run_id, review)
        return {
            "model_plan": plan,
            "field_aliases": aliases,
            "model_plan_review": review,
            "model_gate": {
                "title": "确认候选模型与评分参数",
                "summary": {"plan": plan, "review": review},
                "editable": ["models", "score", "search_budget", "objective"],
            },
        }

    def finalize_model_plan(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        edits = (state.get("model_decision") or {}).get("edits") or {}
        plan = {**state["model_plan"]}
        if "models" in edits:
            availability = available_models()
            requested = list(dict.fromkeys(edits["models"]))
            unavailable = [name for name in requested if not availability.get(name, False)]
            if unavailable:
                raise ValueError("MODEL_SELECTION_UNAVAILABLE")
            if not requested:
                raise ValueError("NO_AVAILABLE_MODELS")
            plan["models"] = requested
            plan["unavailable_models"] = []
        if "score" in edits:
            plan["score"] = {**plan["score"], **edits["score"]}
        if "search_budget" in edits:
            plan["search_budget"] = max(0, min(int(edits["search_budget"]), 12))
        _validate_score_config(plan["score"])
        run = self.catalog.require("runs", run_id)
        self.database.update(
            "target_tasks",
            run["target_task_id"],
            {"model_plan_json": plan, "updated_at": now_iso()},
        )
        from app.domain.optimization import Objective
        from app.governance.manifest import canonical_hash

        objective = Objective.model_validate(
            edits.get("objective", state.get("requested_objective")) or {}
        ).model_dump()
        active_plan = {
            "models": plan["models"],
            "features": state["screening"]["included"],
            "parameters": {},
            "search_budget": plan["search_budget"] if objective["strategy"] != "fixed" else 0,
        }
        if objective["strategy"] == "parameter_search":
            active_plan["search_budget"] = max(1, active_plan["search_budget"])
        snapshot = {
            "schema_version": "risk-objective-snapshot/v1",
            "objective": objective,
            "target": state["target"],
            "dataset_version_id": state["working_dataset_version_id"],
            "split_hash": canonical_hash(state["split"]),
            "score": plan["score"],
            "allowed_features": state["screening"]["included"],
            "forbidden": ["label", "split", "oot", "metric", "code", "external_data"],
        }
        snapshot["version"] = canonical_hash(snapshot)
        if self._estimate_fits(state, active_plan) > objective["max_candidate_fits"]:
            raise ValueError("INITIAL_FIT_BUDGET_INSUFFICIENT")
        return {
            "model_plan": plan,
            "active_plan": active_plan,
            "objective_snapshot": snapshot,
            "optimization_deadline": time.time() + objective["max_seconds"],
        }

    def train_and_review(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        from app.domain.optimization import candidate_rank, plan_hash
        from app.workers.io import ResourcePlan

        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        plan = state["active_plan"]
        objective = state["objective_snapshot"]["objective"]
        rounds = list(state.get("optimization_rounds", []))
        round_id = len(rounds)
        reserved = self._estimate_fits(state, plan)
        if state.get("candidate_fits_reserved", 0) + reserved > objective["max_candidate_fits"]:
            raise ValueError("FIT_BUDGET_EXCEEDED")
        started = time.time()
        if started >= state["optimization_deadline"]:
            raise ValueError("OPTIMIZATION_TIME_BUDGET_EXCEEDED")
        result, bundles = train_candidates(
            frame,
            state["target"],
            plan["features"],
            state["split"],
            models=plan["models"],
            resource=ResourcePlan(**state["model_plan"]["resource_plan"]),
            score_config=state["model_plan"]["score"],
            search_budget=plan["search_budget"],
            evaluate_oot=False,
            candidate_parameters=plan.get("parameters"),
        )
        successful = [c for c in result["candidates"] if c["status"] == "trained"]
        eligible = [c for c in successful if c["candidate"] != "dummy"] or successful
        champion = max(eligible, key=lambda c: candidate_rank(c, objective, len(plan["features"])))
        result["champion"] = champion["candidate"]
        result["champion_metrics"] = {
            "train": champion["train_metrics"],
            "test": champion["test_metrics"],
            "oot": None,
        }
        reviewer = self._reviewer(run_id)
        safe, _ = build_safe_evidence(
            state["profile"], state["target_evidence"], state["screening"], result
        )
        review = reviewer.combine(
            "execution", reviewer.review_execution(result), reviewer.llm_review("execution", safe)
        )
        self._record_review(run_id, review)
        if review_blocks_progress(review) or not review_is_approved(review):
            raise ValueError("EXECUTION_REVIEW_BLOCKED")
        rank = candidate_rank(champion, objective, len(plan["features"]))
        previous_rank = tuple(state.get("best_rank", [False, -1]))
        improved = (
            not rounds
            or rank[0] > previous_rank[0]
            or (
                rank[0] == previous_rank[0]
                and rank[1] > previous_rank[1] + objective["min_improvement"]
            )
        )
        manifest_hash = self._persist_bundles(run_id, bundles, round_id)
        fits = sum(c.get("fit_count", 0) for c in result["candidates"])
        record = {
            "round_id": round_id,
            "parent_hash": (state.get("pending_patch") or {}).get("parent_hash"),
            "plan_hash": plan_hash(plan),
            "plan": plan,
            "patch": state.get("pending_patch"),
            "approval": state.get("optimization_decision")
            if round_id
            else state.get("model_decision"),
            "execution_review": review,
            "result": result,
            "replaced_best": improved,
            "best_delta": None if not rounds else rank[1] - previous_rank[1],
            "previous_delta": None if not rounds else rank[1] - rounds[-1]["rank"][1],
            "rank": list(rank),
            "duration_seconds": time.time() - started,
            "candidate_fits": fits,
            "candidate_fits_reserved": reserved,
            "bundle_manifest_sha256": manifest_hash,
        }
        rounds.append(record)
        update = {
            "optimization_rounds": rounds,
            "last_model_result": result,
            "execution_review": review,
            "candidate_fits_used": state.get("candidate_fits_used", 0) + fits,
            "candidate_fits_reserved": state.get("candidate_fits_reserved", 0) + reserved,
            "no_improvement_count": 0 if improved else state.get("no_improvement_count", 0) + 1,
        }
        if improved:
            update.update(
                {
                    "best_rank": list(rank),
                    "best_round_id": round_id,
                    "model_result": result,
                    "best_manifest_sha256": manifest_hash,
                    "effective_models": list(bundles),
                }
            )
        return update

    def diagnose_optimization(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        from app.domain.optimization import (
            diagnostic_evidence,
            local_proposal,
            validate_patch,
            plan_hash,
        )

        objective = state["objective_snapshot"]["objective"]
        rounds = state["optimization_rounds"]
        reason = None
        if state["best_rank"][0] and state["best_rank"][1] >= objective["target_value"]:
            reason = "target_met"
        elif objective["strategy"] != "agent":
            reason = "baseline_strategy_complete"
        elif len(rounds) > objective["max_optimizations"]:
            reason = "optimization_budget_exhausted"
        elif state["no_improvement_count"] >= objective["patience"]:
            reason = "no_improvement"
        elif time.time() >= state["optimization_deadline"]:
            reason = "time_budget_exhausted"
        evidence = diagnostic_evidence(state["last_model_result"], len(rounds) - 1)
        if reason:
            return {"optimization_stop_reason": reason, "optimization_evidence": evidence}
        parent = state["active_plan"]
        gateway = self._gateway(run_id)
        source = "deterministic_policy"
        if gateway.enabled:
            from app.domain.optimization import PARAMETERS

            payload, response = gateway.complete_json(
                "你是受控建模优化 Agent。只基于开发证据生成 risk-patch-plan/v1 JSON。禁止改变标签、划分、目标、OOT、评分协议或执行代码。输出 parent_hash、diagnosis、evidence_refs、reason、expected_effect、models、parameters；只用提供的参数域。",
                {
                    "evidence": evidence,
                    "parent_hash": plan_hash(parent),
                    "legal_parameters": PARAMETERS,
                    "objective": objective,
                    "prior_plan_hashes": [r["plan_hash"] for r in rounds],
                },
                purpose="main_agent_optimization_patch",
            )
            if not payload:
                return {
                    "optimization_stop_reason": "provider_unavailable",
                    "optimization_evidence": evidence,
                }
            source = "configured_provider"
        else:
            payload = local_proposal(parent, evidence, len(rounds))
        try:
            patch, proposed = validate_patch(
                payload,
                parent,
                state["screening"]["included"],
                [m for m, ok in available_models().items() if ok],
                list(evidence),
            )
            if plan_hash(proposed) in {r["plan_hash"] for r in rounds}:
                return {
                    "optimization_stop_reason": "repeated_plan",
                    "optimization_evidence": evidence,
                }
            estimate = self._estimate_fits(state, proposed)
            if state["candidate_fits_reserved"] + estimate > objective["max_candidate_fits"]:
                return {
                    "optimization_stop_reason": "fit_budget_exhausted",
                    "optimization_evidence": evidence,
                }
        except (ValueError, TypeError, KeyError):
            return {
                "optimization_stop_reason": "illegal_patch",
                "optimization_evidence": evidence,
                "patch_rejection": {"code": "PATCH_VALIDATION_FAILED", "source": source},
            }
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_plan(
            {"split": state["split_plan"], "models": proposed["models"]},
            state["diagnostics"],
            state["screening"],
        )
        review = reviewer.combine(
            "optimization",
            deterministic,
            reviewer.llm_review(
                "optimization",
                {
                    "development_evidence": evidence,
                    "plan": {"models": proposed["models"], "parameters": proposed["parameters"]},
                },
            ),
        )
        self._record_review(run_id, review)
        return {
            "pending_patch": patch,
            "proposed_plan": proposed,
            "optimization_evidence": evidence,
            "optimization_review": review,
            "optimization_stop_reason": None,
            "optimization_gate": {
                "title": f"确认第 {len(rounds)} 次优化方案",
                "editable": [],
                "summary": {
                    "patch": patch,
                    "source": source,
                    "evidence": evidence,
                    "estimated_fits": estimate,
                    "review": review,
                },
            },
        }

    def apply_optimization(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        from app.domain.optimization import validate_patch

        decision = state.get("optimization_decision") or {}
        if decision.get("approved") is not True or decision.get("edits"):
            raise ValueError("OPTIMIZATION_APPROVAL_REQUIRED")
        _, plan = validate_patch(
            state["pending_patch"],
            state["active_plan"],
            state["screening"]["included"],
            [m for m, ok in available_models().items() if ok],
            list(state["optimization_evidence"]),
        )
        return {"active_plan": plan}

    def finalize_optimization(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        from copy import deepcopy
        from app.workers.modeling import evaluate_final_holdout

        result = deepcopy(state["model_result"])
        bundles = self._load_bundles(run_id, state["best_manifest_sha256"], state["best_round_id"])
        if not bundles:
            raise ValueError("BEST_MODEL_BUNDLE_MISSING")
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        evaluate_final_holdout(
            frame, state["target"], state["split"], result, bundles[result["champion"]]
        )
        bundles[result["champion"]].metrics = next(
            c for c in result["candidates"] if c["candidate"] == result["champion"]
        )
        self._bundles[run_id] = bundles
        return {
            "model_result": result,
            "worker_bundle_manifest_sha256": self._persist_bundles(run_id, bundles),
            "goal_status": "met"
            if state["best_rank"][0]
            and state["best_rank"][1] >= state["objective_snapshot"]["objective"]["target_value"]
            else "unmet",
        }

    def _estimate_fits(self, state: dict, plan: dict) -> int:
        # CV search + out-of-fold + up to three calibration variants (1/3/3 fits).
        return sum(
            (5 * min(plan.get("search_budget", 0), 12) if m not in {"dummy", "scorecard"} else 0)
            + 12
            for m in plan["models"]
        )

    def build_and_review_report(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        run = self.catalog.require("runs", run_id)
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        report_run = {**run, "status": "succeeded", "stage": "completed"}
        report = self.artifacts.build_structured_report(report_run, state, frame)
        reviewer = self._reviewer(run_id)
        deterministic = reviewer.review_report(report)
        safe, _ = build_safe_evidence(
            state["profile"], state["target_evidence"], state["screening"], state["model_result"]
        )
        final_review = reviewer.combine(
            "report", deterministic, reviewer.llm_review("report", safe)
        )
        self._record_review(run_id, final_review)
        if review_blocks_progress(final_review) or not review_is_approved(final_review):
            raise ValueError("REPORT_REVIEW_BLOCKED")
        return {"report": report, "report_review": final_review}

    def write_artifacts(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        run = self.catalog.require("runs", run_id)
        task = self.catalog.require("target_tasks", run["target_task_id"])
        frame, _ = freeze_target_samples(self._working_frame(state), state["target"])
        bundles = self._bundles.get(run_id)
        if not bundles:
            bundles = self._load_bundles(run_id, state.get("worker_bundle_manifest_sha256"))
            if bundles:
                self._bundles[run_id] = bundles
        if not bundles or state["model_result"]["champion"] not in bundles:
            raise ValueError("BEST_MODEL_BUNDLE_MISSING")
        champion = state["model_result"]["champion"]
        model_version, package_manifest, model_artifact = self.artifacts.write_model_artifacts(
            run, task, bundles[champion], frame
        )
        report_run = {**run, "status": "succeeded", "stage": "completed"}
        report = self.artifacts.build_structured_report(report_run, state, frame)
        report["artifacts"] = [
            {"name": model_artifact["name"], "kind": "model_package"},
        ]
        report, report_artifacts = self.artifacts.write_report_artifacts(report_run, report)
        return {
            "report": report,
            "model_version_id": model_version["id"],
            "package_manifest": package_manifest,
            "artifact_ids": [model_artifact["id"], *[item["id"] for item in report_artifacts]],
        }

    def _context(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
        run = self.catalog.require("runs", run_id)
        task = self.catalog.require("target_tasks", run["target_task_id"])
        dataset = self.catalog.require("dataset_versions", task["dataset_version_id"])
        budget = SettingsStore(self.paths).load().memory_budget_mb
        return (
            task,
            dataset,
            self.catalog.read_verified_frame(dataset, memory_budget_mb=budget),
        )

    def _working_frame(self, state: dict[str, Any]) -> pd.DataFrame:
        dataset = self.catalog.require("dataset_versions", state["working_dataset_version_id"])
        budget = SettingsStore(self.paths).load().memory_budget_mb
        return self.catalog.read_verified_frame(dataset, memory_budget_mb=budget)

    def _reviewer(self, run_id: str) -> IndependentReviewer:
        return self.reviewer_factory(self._gateway(run_id))

    def _bundle_dir(self, run_id: str, round_id: int | None = None) -> Path:
        run = self.catalog.require("runs", run_id)
        return self.artifacts.run_dir(run["project_id"], run_id) / (
            ".worker-bundles" if round_id is None else f".round-{round_id}-bundles"
        )

    def _persist_bundles(
        self, run_id: str, bundles: dict[str, ModelBundle], round_id: int | None = None
    ) -> str:
        try:
            import skops.io as sio
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise RuntimeError("SKOPS_DEPENDENCY_REQUIRED") from exc
        destination = self._bundle_dir(run_id, round_id)
        stage = Path(tempfile.mkdtemp(prefix=".worker-bundles-", dir=destination.parent))
        manifest: dict[str, Any] = {
            "schema_version": "risk-worker-bundles/v1",
            "skops_policy": SKOPS_POLICY_VERSION,
            "bundles": {},
        }
        try:
            for name, bundle in bundles.items():
                path = stage / f"{name}.skops"
                sio.dump(bundle.estimator, path)
                trusted = inspect_skops_types(path, bundle.algorithm)
                manifest["bundles"][name] = {
                    "file": path.name,
                    "sha256": sha256_file(path),
                    "algorithm": bundle.algorithm,
                    "name": bundle.name,
                    "features": bundle.features,
                    "calibration": bundle.calibration,
                    "score_config": bundle.score_config,
                    "metrics": bundle.metrics,
                    "trusted_types": trusted,
                }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_serializable),
                encoding="utf-8",
            )
            if destination.exists():
                shutil.rmtree(destination)
            stage.replace(destination)
            return sha256_file(destination / "manifest.json")
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _load_bundles(
        self, run_id: str, expected_manifest_sha256: str | None, round_id: int | None = None
    ) -> dict[str, ModelBundle]:
        directory = self._bundle_dir(run_id, round_id)
        manifest_path = directory / "manifest.json"
        if not expected_manifest_sha256 or not manifest_path.is_file():
            return {}
        if sha256_file(manifest_path) != expected_manifest_sha256:
            raise ValueError("WORKER_BUNDLE_MANIFEST_CHECKSUM_MISMATCH")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("WORKER_BUNDLE_MANIFEST_INVALID") from exc
        if (
            manifest.get("schema_version") != "risk-worker-bundles/v1"
            or manifest.get("skops_policy") != SKOPS_POLICY_VERSION
            or not isinstance(manifest.get("bundles"), dict)
        ):
            raise ValueError("WORKER_BUNDLE_MANIFEST_INVALID")
        result: dict[str, ModelBundle] = {}
        for key, details in manifest["bundles"].items():
            if not isinstance(details, dict):
                raise ValueError("WORKER_BUNDLE_MANIFEST_INVALID")
            filename = str(details.get("file") or "")
            if Path(filename).name != filename:
                raise ValueError("WORKER_BUNDLE_PATH_INVALID")
            path = directory / filename
            if not path.is_file() or sha256_file(path) != details.get("sha256"):
                raise ValueError("WORKER_BUNDLE_CHECKSUM_MISMATCH")
            estimator = load_skops_model(
                path,
                str(details.get("algorithm") or ""),
                details.get("trusted_types") or [],
            )
            result[str(key)] = ModelBundle(
                str(details.get("name") or key),
                str(details["algorithm"]),
                estimator,
                list(details.get("features") or []),
                str(details.get("calibration") or "uncalibrated"),
                dict(details.get("score_config") or {}),
                dict(details.get("metrics") or {}),
            )
        return result

    def _gateway(self, run_id: str) -> ProviderGateway:
        settings = SettingsStore(self.paths).load()

        def request_callback(purpose: str, evidence: dict[str, Any], model: str) -> str:
            identifier = new_id("provider")
            digest = sha256_bytes(
                json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str).encode(
                    "utf-8"
                )
            )
            span = self.traces.start_span(
                run_id,
                kind="llm",
                stage=self.catalog.require("runs", run_id)["stage"],
                node=purpose,
                agent="reviewer_agent" if purpose.startswith("reviewer_") else "main_agent",
                tool="provider_gateway",
                parent_span_id=self._active_parent_span_id,
                summary=f"Provider request: {purpose}",
                input_payload=evidence,
                evidence={
                    "purpose": purpose,
                    "provider": settings.provider,
                    "model": model,
                    "safe_payload_hash": digest,
                },
            )
            self.database.insert(
                "provider_requests",
                {
                    "id": identifier,
                    "run_id": run_id,
                    "provider": settings.provider,
                    "model": model,
                    "status": "requested",
                    "safe_payload_hash": digest,
                    "usage_json": {
                        "purpose": purpose,
                        "span_id": span["id"],
                        "attempt": 1,
                    },
                    "response_summary": "",
                    "created_at": now_iso(),
                },
            )
            return identifier

        def result_callback(identifier: str, result: Any) -> None:
            record = self.database.get("provider_requests", identifier)
            if not record:
                return
            usage = dict(record.get("usage") or {})
            usage.update(result.usage or {})
            usage["model"] = result.model
            usage["error_code"] = result.error_code
            usage["error_type"] = result.error_type
            usage["http_status"] = result.http_status
            usage["duration_ms"] = result.duration_ms
            usage["response_hash"] = result.response_hash
            if result.upstream_request_id:
                usage["upstream_request_id"] = result.upstream_request_id
            if result.ok:
                terminal_status = "succeeded"
            elif result.error_code in {"DLP_BLOCK", "PROVIDER_BUDGET_EXCEEDED"}:
                terminal_status = "blocked"
            elif result.error_code in {"PROVIDER_CANCELLED", "PROVIDER_DISABLED"}:
                terminal_status = "cancelled"
            else:
                terminal_status = "failed"
            self.database.update(
                "provider_requests",
                identifier,
                {
                    "status": terminal_status,
                    "usage_json": usage,
                    "response_summary": (
                        "ok"
                        if result.ok
                        else str(result.error_code or "PROVIDER_REQUEST_FAILED")[:300]
                    ),
                },
            )
            span_id = str(usage.get("span_id") or "")
            if span_id:
                self.traces.finish_span(
                    span_id,
                    terminal_status,
                    output_payload={"response_hash": result.response_hash},
                    error_code=result.error_code,
                    error_type=result.error_type,
                    usage={
                        key: value
                        for key, value in usage.items()
                        if key not in {"span_id", "response_hash"}
                    },
                    security={
                        "safe_evidence_only": True,
                        "payload_hash": result.payload_hash,
                    },
                    evidence={"provider_request_id": identifier},
                )

        def budget_guard(requested: int) -> str | None:
            records = self.database.list_all("provider_requests", {"run_id": run_id})
            used = sum(int((item.get("usage") or {}).get("total_tokens") or 0) for item in records)
            if settings.run_token_budget and used + requested > settings.run_token_budget:
                return "本 Run 的 Token 预算不足。"
            if settings.monthly_token_budget:
                current_month = now_iso()[:7]
                monthly = self.database.list_all("provider_requests")
                monthly_used = sum(
                    int((item.get("usage") or {}).get("total_tokens") or 0)
                    for item in monthly
                    if str(item.get("created_at", ""))[:7] == current_month
                )
                if monthly_used + requested > settings.monthly_token_budget:
                    return "本月 Token 预算不足。"
            return None

        return ProviderGateway(
            settings=settings,
            api_key=self.provider_api_key,
            client_factory=self.provider_client_factory,
            budget_guard=budget_guard,
            request_callback=request_callback,
            result_callback=result_callback,
            paths=self.paths,
        )

    def _record_review(self, run_id: str, review: dict[str, Any]) -> dict[str, Any]:
        existing = self.database.list(
            "review_records", {"run_id": run_id}, order_by="round ASC", limit=500
        )
        record = self.database.insert(
            "review_records",
            {
                "id": new_id("rev"),
                "run_id": run_id,
                "round": len(existing) + 1,
                "scope": review["scope"],
                "status": review["status"],
                "issues_json": review.get("issues", []),
                "evidence_json": review.get("evidence", {}),
                "created_at": now_iso(),
            },
        )
        span = self.traces.start_span(
            run_id,
            kind="reviewer",
            stage=self.catalog.require("runs", run_id)["stage"],
            node=f"review_{review['scope']}",
            agent="reviewer_agent",
            tool="independent_reviewer",
            parent_span_id=self._active_parent_span_id,
            summary=f"Reviewer: {review['scope']}",
            input_payload={
                "scope": review["scope"],
                "issue_codes": [item.get("code") for item in review.get("issues", [])],
            },
            attempt=int(review.get("repair_round") or 1),
            evidence={"review_record_id": record["id"]},
        )
        status = str(review.get("status") or "revise")
        terminal = (
            "succeeded"
            if review_is_approved(status)
            else ("blocked" if status in {"block", "blocked"} else "failed")
        )
        self.traces.finish_span(
            span["id"],
            terminal,
            output_payload={"status": status, "issue_count": len(review.get("issues") or [])},
            degradation_path=(
                "deterministic_fallback"
                if status == "fallback_pass"
                else ("revision_required" if status == "revise" else None)
            ),
            evidence={"review_record_id": record["id"], "review_status": status},
        )
        return record


def _first_candidate(profile: dict[str, Any], key: str) -> str | None:
    for item in profile.get("columns_detail", []):
        if item.get(key) and not item.get("pii"):
            return str(item["name"])
    return None


def _preferred_customer_key(profile: dict[str, Any]) -> str | None:
    """Prefer person-level identifiers over order-level identifiers for isolation."""
    candidates = [
        item
        for item in profile.get("columns_detail", [])
        if item.get("id_candidate") and not item.get("pii")
    ]
    if not candidates:
        return None

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("name") or "").lower()
        person_tokens = (
            "customer",
            "cust",
            "user",
            "person",
            "borrower",
            "client",
            "客户",
            "用户",
            "借款人",
        )
        order_tokens = ("order", "loan", "application", "contract", "订单", "借据", "申请")
        score = 2 if any(token in name for token in person_tokens) else 0
        score -= 2 if any(token in name for token in order_tokens) else 0
        # Repeated identifiers are more likely to represent a customer across
        # multiple orders than a row-level technical key.
        rows = max(int(profile.get("rows") or 0), 1)
        repeated = int(item.get("unique_count") or 0) < rows
        return score, int(repeated)

    return str(max(candidates, key=rank).get("name"))


def _validate_score_config(config: dict[str, Any]) -> None:
    minimum = float(config["minimum"])
    maximum = float(config["maximum"])
    if not (0 <= minimum < maximum <= 5000):
        raise ValueError("SCORE_RANGE_INVALID")
    if float(config["base_odds"]) <= 0 or float(config["pdo"]) <= 0:
        raise ValueError("SCORE_SCALING_INVALID")


def _json_serializable(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)
