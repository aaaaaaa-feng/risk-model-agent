from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest


os.environ.setdefault("RISK_AGENT_DATA_DIR", tempfile.mkdtemp(prefix="risk-agent-pytest-global-"))
os.environ.setdefault("RISK_AGENT_AUTO_MIGRATE", "0")
os.environ.setdefault("RISK_AGENT_OPEN_BROWSER", "0")

from app.core.config import SettingsStore  # noqa: E402
from app.core.paths import AppPaths  # noqa: E402
from app.runtime import AppContext  # noqa: E402
from app.workers.demo import install_demo_project  # noqa: E402


def wait_for_run(context: AppContext, run_id: str, states: set[str], timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = context.catalog.require("runs", run_id)
        if run["status"] in states:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach {states}")


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "RiskModelAgent").ensure()


@pytest.fixture
def context(app_paths: AppPaths):
    value = AppContext.create(app_paths)
    yield value
    value.shutdown()


@pytest.fixture(scope="session")
def golden(tmp_path_factory: pytest.TempPathFactory):
    paths = AppPaths(tmp_path_factory.mktemp("golden") / "RiskModelAgent").ensure()
    context = AppContext.create(paths)
    SettingsStore(paths).save(
        {
            "llm_enabled": False,
            "default_models": ["dummy", "scorecard", "regularized_logistic", "xgboost"],
            "max_parallel_models": 1,
        }
    )
    demo = install_demo_project(context.catalog, mode="fully_trusted", rows=800)
    created = context.engine.create_run(
        demo["project"]["id"], demo["target_tasks"][0]["id"], "fully_trusted"
    )
    run = wait_for_run(context, created["id"], {"succeeded", "failed", "blocked"}, 240)
    if run["status"] != "succeeded":
        raise AssertionError(f"Golden run failed: {run.get('error')}")
    yield {"context": context, "demo": demo, "run": run}
    context.shutdown()
