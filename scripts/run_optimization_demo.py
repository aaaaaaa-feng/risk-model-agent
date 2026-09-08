"""Reproducible synthetic demo; actual product workers, explicitly zero LLM calls."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ["RISK_AGENT_DATA_DIR"] = str(root / "workspace")
    os.environ["RISK_AGENT_AUTO_MIGRATE"] = "0"
    os.environ["RISK_AGENT_OPEN_BROWSER"] = "0"
    from app.bootstrap import AppContext
    from app.core.paths import AppPaths
    from app.core.config import SettingsStore
    from app.governance.manifest import discover_git_sha
    from app.services.run_readiness import compare_runs
    from app.workers.demo import install_demo_project

    context = AppContext.create(AppPaths(root / "workspace").ensure())
    SettingsStore(context.paths).save(
        {"llm_enabled": False, "default_models": ["regularized_logistic"], "max_parallel_models": 1}
    )
    summary = {
        "schema_version": "risk-demonstration/v1",
        "git_sha": discover_git_sha(),
        "data": "synthetic_time_oot_v1",
        "rows": 500,
        "mode": "deterministic_product",
        "executor": "WorkerProcessRunner",
        "real_llm_calls": 0,
        "runs": [],
    }
    try:
        demo = install_demo_project(context.catalog, mode="fully_trusted", rows=500)
        completed = []
        for strategy in ("fixed", "parameter_search", "agent"):
            started = time.monotonic()
            run = context.engine.create_run(
                demo["project"]["id"],
                demo["target_tasks"][0]["id"],
                "fully_trusted",
                objective={
                    "strategy": strategy,
                    "target_value": 1.0,
                    "max_candidate_fits": 300,
                    "max_seconds": 300,
                },
            )
            last_seq, last_change, max_silence = 0, started, 0.0
            while time.monotonic() - started < 360:
                run = context.catalog.require("runs", run["id"])
                if run["seq"] != last_seq:
                    max_silence = max(max_silence, time.monotonic() - last_change)
                    last_change, last_seq = time.monotonic(), run["seq"]
                if run["status"] in {"succeeded", "failed", "blocked"}:
                    break
                time.sleep(0.1)
            else:
                context.engine.cancel(run["id"])
                raise RuntimeError("DEMO_TIMEOUT")
            state = run["state"]
            events = context.database.list_all("events", {"run_id": run["id"]}, order_by="seq ASC")
            record = {
                "strategy": strategy,
                "run_id": run["id"],
                "status": run["status"],
                "error": run.get("error"),
                "duration_seconds": time.monotonic() - started,
                "max_event_gap_seconds": max_silence,
                "tool_calls": sum(
                    e["status"] == "completed" and bool(e.get("tool")) for e in events
                ),
                "rounds": state.get("optimization_rounds"),
                "best_round_id": state.get("best_round_id"),
                "stop_reason": state.get("optimization_stop_reason"),
                "goal_status": state.get("goal_status"),
                "metrics": (state.get("model_result") or {}).get("champion_metrics"),
                "delivery_check": (state.get("package_manifest") or {}).get("delivery_check"),
                "objective_snapshot": state.get("objective_snapshot"),
                "candidate_fits": state.get("candidate_fits_used"),
            }
            summary["runs"].append(record)
            completed.append(run)
            print(
                json.dumps(
                    {
                        "strategy": strategy,
                        "status": run["status"],
                        "rounds": len(state.get("optimization_rounds", [])),
                        "seconds": record["duration_seconds"],
                    }
                ),
                flush=True,
            )
        summary["comparisons"] = [
            compare_runs(completed[0], item, context.database) for item in completed[1:]
        ]
        (root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if any(run["status"] != "succeeded" for run in completed):
            raise RuntimeError("DEMO_RUN_FAILED")
    finally:
        context.shutdown()


if __name__ == "__main__":
    main()
