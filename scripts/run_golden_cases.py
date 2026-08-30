"""Run the deterministic end-to-end synthetic V1 release gate."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import SettingsStore  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.bootstrap import AppContext  # noqa: E402
from app.workers.demo import install_demo_project  # noqa: E402


def main() -> int:
    root = Path(os.getenv("RISK_AGENT_GOLDEN_DIR") or tempfile.mkdtemp(prefix="risk-agent-golden-"))
    context = AppContext.create(AppPaths(root).ensure())
    try:
        SettingsStore(context.paths).save(
            {
                "llm_enabled": False,
                "default_models": [
                    "dummy",
                    "scorecard",
                    "regularized_logistic",
                    "xgboost",
                ],
            }
        )
        demo = install_demo_project(context.catalog, mode="fully_trusted", rows=800)
        created = context.engine.create_run(
            demo["project"]["id"], demo["target_tasks"][0]["id"], "fully_trusted"
        )
        deadline = time.monotonic() + 300
        run = created
        while time.monotonic() < deadline:
            run = context.catalog.require("runs", created["id"])
            if run["status"] in {"succeeded", "failed", "blocked"}:
                break
            time.sleep(0.1)
        state = run.get("state") or {}
        candidates = state.get("model_result", {}).get("candidates", [])
        trained = [item["candidate"] for item in candidates if item.get("status") == "trained"]
        result = {
            "schema_version": "risk-golden-regression/v1",
            "synthetic": True,
            "seed": demo["synthetic_evidence"]["seed"],
            "run_id": run["id"],
            "status": run["status"],
            "trained_models": trained,
            "other_targets_blocked": all(
                item.get("reason") == "OTHER_TARGET"
                for item in state.get("screening", {}).get("excluded", [])
                if item.get("column") in {"FPD7", "MOB30"}
            ),
            "oot_used_for_selection": state.get("model_result", {}).get("oot_used_for_selection"),
        }
        result["passed"] = (
            result["status"] == "succeeded"
            and {"dummy", "scorecard", "regularized_logistic", "xgboost"}.issubset(trained)
            and result["other_targets_blocked"] is True
            and result["oot_used_for_selection"] is False
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["passed"] else 1
    finally:
        context.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
