from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from app.core.config import SettingsStore
from app.core.database import Database
from app.core.paths import AppPaths
from app.core.security import sha256_file
from app.evaluation.contracts import EvalCase, EvalResult
from app.evaluation.fakes import ScriptedProviderFactory
from app.governance.manifest import canonical_hash
from app.governance.tracing import TraceService
from app.orchestration.graph import RunEngine
from app.services.artifacts import ArtifactService
from app.services.catalog import CatalogService
from app.services.pipeline import RunPipeline
from app.workers.demo import install_demo_project


class EvaluationToolRunner:
    def __init__(self, pipeline: RunPipeline, faults: list[str]):
        self.pipeline = pipeline
        self.faults = set(faults)

    def invoke(self, tool: str, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        if f"worker_timeout:{tool}" in self.faults:
            raise TimeoutError(f"WORKER_TIMEOUT_INJECTED: {tool}")
        if f"worker_error:{tool}" in self.faults:
            raise RuntimeError(f"WORKER_ERROR_INJECTED: {tool}")
        return self.pipeline.invoke(tool, run_id, state)

    def shutdown(self) -> None:
        return None


def run_eval_case(
    *,
    case: EvalCase | dict[str, Any],
    trial_id: str,
    artifact_root: Path,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one isolated local case through the same graph and deterministic tools.

    This is a stable Target Adapter for a future external Harness. It does not
    contain scoring rubrics, leaderboards, LLM-as-a-Judge, or an evaluation UI.
    """
    parsed = case if isinstance(case, EvalCase) else EvalCase.model_validate(case)
    if (
        not trial_id
        or len(trial_id) > 120
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in trial_id
        )
    ):
        raise ValueError("EVAL_TRIAL_ID_INVALID")
    # Validate external-provider input before creating an isolated directory so
    # an invalid secret/configuration cannot leave a misleading partial trial.
    provider_payload = _provider_settings(parsed.provider_profile, provider)
    case_root = artifact_root.resolve() / parsed.case_id / trial_id
    if case_root.exists():
        raise ValueError("EVAL_TRIAL_ALREADY_EXISTS")
    workspace = case_root / "workspace"
    export_root = case_root / "exports"
    paths = AppPaths(workspace / "RiskModelAgent").ensure()
    export_root.mkdir(parents=True, exist_ok=False)

    engine: RunEngine | None = None
    run_id: str | None = None
    terminal = "setup_failed"
    error: dict[str, Any] | None = None
    trace_path: Path | None = None
    artifact_path: Path | None = None
    try:
        database = Database(paths=paths)
        catalog = CatalogService(database, paths)
        artifacts = ArtifactService(database, paths, catalog)
        fake_provider = ScriptedProviderFactory(parsed.faults)
        llm_enabled = parsed.provider_profile != "deterministic"
        SettingsStore(paths).save(
            {
                "llm_enabled": llm_enabled,
                **provider_payload["settings"],
                "default_models": [
                    "dummy",
                    "scorecard",
                    "regularized_logistic",
                    "xgboost",
                ],
                "max_parallel_models": 1,
            }
        )
        pipeline = RunPipeline(
            database,
            paths,
            catalog,
            artifacts,
            provider_client_factory=(
                fake_provider if parsed.provider_profile == "fake_provider" else None
            ),
            provider_api_key=provider_payload["api_key"],
        )
        runner = EvaluationToolRunner(pipeline, parsed.faults)
        engine = RunEngine(database, paths, catalog, pipeline, worker=runner)
        demo = install_demo_project(
            catalog,
            name=f"Eval · {parsed.case_id}",
            mode=parsed.mode,
            rows=parsed.rows,
        )
        task = next(item for item in demo["target_tasks"] if item["target_column"] == parsed.target)
        created = engine.create_run(
            demo["project"]["id"],
            task["id"],
            parsed.mode,
            evaluation_context={
                "case_id": parsed.case_id,
                "trial_id": trial_id,
                "suite_version": parsed.suite_version,
                "evaluator_version": parsed.evaluator_version,
                "case_config_sha256": canonical_hash(parsed.model_dump(mode="json")),
            },
        )
        run_id = created["id"]
        deadline = time.monotonic() + parsed.timeout_seconds
        used_decisions: set[int] = set()
        while time.monotonic() < deadline:
            run = catalog.require("runs", run_id)
            terminal = str(run["status"])
            if terminal in {"succeeded", "failed", "blocked"}:
                break
            if terminal == "awaiting_decision":
                pending = [
                    item
                    for item in database.list_all("decisions", {"run_id": run_id})
                    if item["status"] == "pending"
                ]
                if pending:
                    decision = pending[-1]
                    selection, selection_index = _select_decision(parsed, decision, used_decisions)
                    if selection_index is not None:
                        used_decisions.add(selection_index)
                    engine.resume(
                        run_id,
                        decision["id"],
                        selection.approved,
                        selection.edits,
                    )
            time.sleep(0.05)
        else:
            terminal = "cancelled"
            error = {"code": "EVAL_TIMEOUT", "type": "TimeoutError"}

        traces = TraceService(database)
        trace_path = traces.export_bundle(run_id, export_root / "trace-bundle.json")
        artifact_path = _write_artifact_manifest(database, run_id, export_root)
        if terminal != parsed.expected_terminal_state and error is None:
            error = {
                "code": "EXPECTED_TERMINAL_STATE_MISMATCH",
                "type": "AssertionError",
                "actual": terminal,
                "expected": parsed.expected_terminal_state,
            }
        bundle = traces.bundle(run_id)
        usage = _aggregate_usage(bundle.get("provider_requests") or [])
        security_events = list(bundle.get("security_events") or [])
    except Exception as exc:
        terminal = "adapter_failed"
        error = {
            "code": str(exc).split(":", 1)[0][:200] or type(exc).__name__,
            "type": type(exc).__name__,
        }
        usage = {}
        security_events = []
    finally:
        if engine is not None:
            engine.shutdown()

    result = EvalResult(
        case_id=parsed.case_id,
        trial_id=trial_id,
        run_id=run_id,
        terminal_state=terminal,
        expected_terminal_state=parsed.expected_terminal_state,
        expectation_met=terminal == parsed.expected_terminal_state and error is None,
        final_response=_final_response(terminal, run_id),
        trace_bundle_path=str(trace_path) if trace_path else None,
        artifact_manifest_path=str(artifact_path) if artifact_path else None,
        security_events=security_events,
        usage=usage,
        error=error,
    )
    result_path = export_root / "result.json"
    result_path.write_text(
        json.dumps(result.model_dump(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if parsed.cleanup_workspace and terminal in {"succeeded", "failed", "blocked"}:
        resolved_workspace = workspace.resolve()
        if resolved_workspace.parent != case_root.resolve():
            raise RuntimeError("EVAL_WORKSPACE_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(resolved_workspace)
    return result.model_dump()


def _select_decision(
    case: EvalCase,
    pending: dict[str, Any],
    used: set[int],
) -> tuple[Any, int | None]:
    for index, decision in enumerate(case.user_decisions):
        if index in used:
            continue
        if decision.stage and decision.stage != pending["stage"]:
            continue
        if decision.kind and decision.kind != pending["kind"]:
            continue
        return decision, index
    from app.evaluation.contracts import EvalDecision

    return EvalDecision(), None


def _provider_settings(profile: str, provider: dict[str, Any] | None) -> dict[str, Any]:
    if profile == "deterministic":
        return {
            "settings": {
                "provider": "custom",
                "api_format": "openai",
                "base_url": "",
                "model": "",
                "reviewer_model": "",
            },
            "api_key": None,
        }
    if profile == "fake_provider":
        return {
            "settings": {
                "provider": "custom",
                "api_format": "openai",
                "base_url": "https://eval.invalid/v1",
                "model": "fake-main-v1",
                "reviewer_model": "fake-reviewer-v1",
            },
            "api_key": "eval-key-not-real",
        }
    raw = dict(provider or {})
    api_key = str(raw.pop("api_key", "")).strip()
    allowed = {"provider", "api_format", "base_url", "model", "reviewer_model"}
    if (
        not api_key
        or not str(raw.get("base_url") or "").strip()
        or not str(raw.get("model") or "").strip()
    ):
        raise ValueError("EVAL_PROVIDER_CONFIG_INCOMPLETE")
    if set(raw) - allowed:
        raise ValueError("EVAL_PROVIDER_CONFIG_FIELD_UNSUPPORTED")
    settings = {
        "provider": str(raw.get("provider") or "custom"),
        "api_format": "anthropic" if raw.get("api_format") == "anthropic" else "openai",
        "base_url": str(raw["base_url"]),
        "model": str(raw["model"]),
        "reviewer_model": str(raw.get("reviewer_model") or raw["model"]),
    }
    return {"settings": settings, "api_key": api_key}


def _write_artifact_manifest(database: Database, run_id: str, root: Path) -> Path:
    artifacts = database.list_all("artifacts", {"run_id": run_id}, order_by="created_at ASC")
    models = database.list_all("model_versions", {"run_id": run_id}, order_by="created_at ASC")
    verified_artifacts: list[dict[str, Any]] = []
    application_root = database.paths.root.resolve()
    for item in artifacts:
        path = Path(str(item["path"])).resolve()
        if application_root not in path.parents:
            raise ValueError("EVAL_ARTIFACT_PATH_OUTSIDE_WORKSPACE")
        if not path.is_file() or sha256_file(path) != item["checksum"]:
            raise ValueError("EVAL_ARTIFACT_CHECKSUM_MISMATCH")
        verified_artifacts.append(
            {
                "id": item["id"],
                "kind": item["kind"],
                "checksum": item["checksum"],
                "size_bytes": path.stat().st_size,
                "mime_type": item["mime_type"],
                "verified": True,
            }
        )
    payload = {
        "schema_version": "risk-agent-eval-artifacts/v1",
        "run_id": run_id,
        "artifacts": verified_artifacts,
        "models": [
            {
                "id": item["id"],
                "algorithm": item["algorithm"],
                "status": item["status"],
                "checksum": item.get("checksum"),
                "champion": bool(item.get("champion")),
            }
            for item in models
        ],
    }
    destination = root / "artifact-manifest.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return destination


def _aggregate_usage(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "provider_request_count": len(requests),
        "total_tokens": sum(
            int((item.get("usage") or {}).get("total_tokens") or 0) for item in requests
        ),
        "status_counts": {
            status: sum(1 for item in requests if item.get("status") == status)
            for status in ("succeeded", "failed", "blocked", "cancelled")
        },
    }


def _final_response(status: str, run_id: str | None) -> str:
    if status == "succeeded":
        return f"Run {run_id} 已在隔离环境中完成；请以 Trace 和产物 Manifest 为准。"
    return f"Run {run_id or '未创建'} 终态为 {status}；请查看结构化错误与 Trace。"
