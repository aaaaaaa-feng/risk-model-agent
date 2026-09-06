from types import SimpleNamespace

import numpy as np
import pytest

from app.agents.reviewer import IndependentReviewer
from app.core.database import Database
from app.orchestration.process_runner import WorkerProcessRunner
from app.workers.metrics import psi


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "pass", "issues": [None]},
        {"status": "pass", "issues": "invalid"},
        {"status": "pass", "issues": [{"severity": "blocking"}]},
        {"status": [], "issues": []},
    ],
)
def test_invalid_reviewer_payload_becomes_explicit_revision(payload):
    gateway = SimpleNamespace(
        enabled=True,
        settings=SimpleNamespace(reviewer_model="fake", model="fake", provider="fake"),
        complete_json=lambda *args, **kwargs: (
            payload,
            SimpleNamespace(model="fake", payload_hash="fake", error_code=None),
        ),
    )
    reviewer = IndependentReviewer(gateway)
    review = reviewer.combine("target", {"issues": []}, reviewer.llm_review("target", {}))
    assert review["status"] == "revise"
    assert review["issues"][0]["code"] == "REVIEWER_RESPONSE_SCHEMA_INVALID"


def test_psi_detects_constant_and_binary_distribution_shifts():
    assert psi(np.full(100, 0.2), np.full(100, 0.2)) == pytest.approx(0)
    assert psi(np.full(100, 0.2), np.full(100, 0.8)) > 1
    assert psi(np.tile([0, 1], 50), np.ones(100)) > 1
    assert psi(np.array([np.nan]), np.array([1.0])) is None


def test_database_rejects_injected_filter_columns(app_paths):
    database = Database(paths=app_paths)
    with pytest.raises(ValueError, match="UNKNOWN_COLUMN"):
        database.list("projects", {"1=1 OR name": "ignored"})


def test_worker_cannot_start_after_shutdown(app_paths):
    runner = WorkerProcessRunner(app_paths)
    runner.shutdown()
    with pytest.raises(RuntimeError, match="WORKER_RUNNER_CLOSED"):
        runner.invoke("does_not_exist", "not_a_run", {})
