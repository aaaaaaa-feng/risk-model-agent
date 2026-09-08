from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.providers.gateway import ProviderGateway
from app.services.run_readiness import compare_runs


def test_provider_budget_reserves_input_and_output_before_network(app_paths):
    reservations = []

    def guard(tokens):
        reservations.append(tokens)
        return "bounded"

    gateway = ProviderGateway(
        settings=Settings(llm_enabled=True, base_url="https://eval.invalid/v1", model="fake"),
        api_key="fake-not-real",
        paths=app_paths,
        budget_guard=guard,
    )
    result = gateway.complete("long system " * 100, {"summary": "x" * 500}, max_tokens=10)
    assert result.error_code == "PROVIDER_BUDGET_EXCEEDED"
    assert reservations[0] > 1500


def test_compare_rejects_changed_cleaned_data_even_with_same_source():
    class Database:
        def list(self, *args, **kwargs):
            return [{"payload": {"dataset": {"content_sha256": "same-source"}}}]

        def list_all(self, *args, **kwargs):
            return []

    base = {
        "id": "a",
        "project_id": "p",
        "status": "succeeded",
        "state": {
            "objective_snapshot": {
                "target": "Y",
                "working_data_sha256": "a",
                "split_hash": "same",
                "score": {},
                "objective": {},
            }
        },
    }
    other = {
        **base,
        "id": "b",
        "state": {
            "objective_snapshot": {
                **base["state"]["objective_snapshot"],
                "working_data_sha256": "b",
            }
        },
    }
    result = compare_runs(base, other, Database())
    assert not result["directly_comparable"]
    assert "working_data_sha256_different" in result["reasons"]


def test_product_worker_passes_ephemeral_key_outside_public_state(app_paths, monkeypatch):
    from app.orchestration.process_runner import WorkerProcessRunner, _pipeline_process_entry
    from app.services import pipeline as module

    observed = {}

    class Pipeline:
        def __init__(self, *args, **kwargs):
            observed["key"] = kwargs.get("provider_api_key")

        def invoke(self, tool, run_id, state):
            return {"configured": bool(observed["key"])}

    monkeypatch.setattr(module, "RunPipeline", Pipeline)
    output = app_paths.root / "output.json"
    _pipeline_process_entry(
        str(app_paths.root), "test", "run-test", {}, "ephemeral-test-only", str(output)
    )
    assert json.loads(output.read_text())["result"]["configured"]
    assert "ephemeral-test-only" not in output.read_text()
    runner = WorkerProcessRunner(app_paths, provider_api_key="ephemeral-test-only")

    def capture(target, arguments, label, **kwargs):
        assert arguments[-1] == "ephemeral-test-only"
        assert "ephemeral-test-only" not in json.dumps(arguments[-2])
        return {}

    monkeypatch.setattr(runner, "_run", capture)
    runner.invoke("test", "run-test", {})
    runner.shutdown()


def test_real_eval_requires_finite_token_budget():
    from app.evaluation.adapter import _provider_settings

    payload = {"base_url": "https://eval.invalid/v1", "model": "fake", "api_key": "fake-not-real"}
    with pytest.raises(ValueError, match="EVAL_PROVIDER_TOKEN_BUDGET_REQUIRED"):
        _provider_settings("configured_provider", payload)
    assert (
        _provider_settings("configured_provider", {**payload, "run_token_budget": 10000})[
            "settings"
        ]["run_token_budget"]
        == 10000
    )


def test_worker_terminates_descendants_before_success(monkeypatch):
    import psutil
    from app.orchestration.process_runner import WorkerProcessRunner

    calls = []

    class Child:
        def terminate(self):
            calls.append("child_terminate")

    child = Child()

    class Parent:
        pid = 123
        alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("parent_terminate")
            self.alive = False

        def join(self, seconds):
            pass

    class Inspection:
        def children(self, recursive):
            return [child]

    monkeypatch.setattr(psutil, "Process", lambda pid: Inspection())
    monkeypatch.setattr(psutil, "wait_procs", lambda children, timeout: (children, []))
    WorkerProcessRunner._terminate(Parent())
    assert calls == ["child_terminate", "parent_terminate"]


def test_rate_limit_retry_is_bounded_and_separately_named(app_paths, monkeypatch):
    from app.providers.gateway import ProviderResult

    gateway = ProviderGateway(settings=Settings(), paths=app_paths)
    purposes = []

    def limited(*args, **kwargs):
        purposes.append(kwargs["purpose"])
        return ProviderResult(False, error_code="PROVIDER_RATE_LIMITED")

    monkeypatch.setattr(gateway, "complete", limited)
    payload, result = gateway.complete_json("system", {}, purpose="optimization")
    assert payload is None and result.error_code == "PROVIDER_RATE_LIMITED"
    assert purposes == ["optimization", "optimization_retry_1"]


def _writing_worker(marker: str, output: str):
    import time
    from pathlib import Path

    while True:
        Path(marker).write_text(str(time.time()))
        time.sleep(0.03)


def test_product_worker_deadline_stops_real_writes(app_paths):
    import time
    from app.orchestration.process_runner import WorkerProcessRunner

    marker = app_paths.root / "writer-marker"
    runner = WorkerProcessRunner(app_paths)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="WORKER_TIMEOUT"):
        runner._run(_writing_worker, (str(marker),), "deadline-proof", deadline=time.time() + 2)
    assert time.monotonic() - started < 8
    assert not runner._active
    assert marker.exists()
    last = marker.read_text()
    time.sleep(0.2)
    assert marker.read_text() == last
    runner.shutdown()
