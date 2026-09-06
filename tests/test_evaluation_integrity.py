from __future__ import annotations

import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.evaluation.contracts import EvalCase, EvalGate, EvalSuite
from app.evaluation.harness import EvaluationHarness, _evaluate_gate, _summarize
from app.evaluation.adapter import run_eval_case
from app.evaluation.integrity import trace_errors
from app.governance.manifest import canonical_hash


def suite():
    return EvalSuite(suite_id="audit_suite", name="Audit", cases=[EvalCase(case_id="audit_case")])


def test_suite_identity_is_immutable_and_retry_is_idempotent(app_paths):
    harness = EvaluationHarness(app_paths)
    try:
        definition = suite()
        first = harness.save_suite(definition)
        assert harness.save_suite(definition) == first
        changed = definition.model_copy(update={"trials": 2})
        with pytest.raises(ValueError, match="EVAL_SUITE_IMMUTABLE"):
            harness.save_suite(changed)
        assert harness.get_suite(definition.suite_id) == first
    finally:
        harness.shutdown()


@pytest.mark.parametrize("identifier", [".", ".."])
def test_run_identifier_cannot_escape_registry(app_paths, identifier):
    harness = EvaluationHarness(app_paths)
    try:
        with pytest.raises(ValueError, match="EVAL_RUN_ID_INVALID"):
            harness.get_run(identifier)
    finally:
        harness.shutdown()


def test_gate_cannot_average_away_critical_case_failure(tmp_path):
    definition = EvalSuite(
        suite_id="critical_gate",
        name="Critical gate",
        cases=[EvalCase(case_id="critical_case", severity="critical")],
        gate=EvalGate(
            min_expectation_rate=0,
            max_error_rate=1,
            max_security_event_rate=1,
            require_trace_for_each_case=False,
        ),
    )
    results = [
        {
            "case_id": "critical_case",
            "trial_id": "trial_001",
            "severity": "critical",
            "category": "core",
            "expectation_met": False,
            "terminal_state": "failed",
            "expected_terminal_state": "succeeded",
            "error": {"code": "EXPECTED_TERMINAL_STATE_MISMATCH"},
        }
    ]
    gate = _evaluate_gate(_summarize(results, definition), definition)
    assert gate["passed"] is False


def test_gate_rejects_forged_complete_trace(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            {
                "trace": {"id": "trace_1", "root_span_id": "span_1"},
                "spans": [{"id": "span_1"}],
                "events": [],
                "raw_records_included": True,
                "hidden_chain_of_thought_included": True,
            }
        )
    )
    definition = suite()
    results = [
        {
            "case_id": "audit_case",
            "trial_id": "trial_001",
            "severity": "medium",
            "category": "core",
            "expectation_met": True,
            "trace_bundle_path": str(trace),
            "terminal_state": "succeeded",
            "expected_terminal_state": "succeeded",
        }
    ]
    assert _evaluate_gate(_summarize(results, definition), definition)["passed"] is False


def test_baseline_pairs_trials_by_identity_and_rejects_missing_or_tampered_evidence(app_paths):
    harness = EvaluationHarness(app_paths)
    try:
        definition = suite().model_copy(update={"trials": 2})
        harness.save_suite(definition)
        baseline = harness.run_now(definition.suite_id)
        assert baseline["gate"]["passed"] is True
        assert baseline["suite_sha256"] == canonical_hash(baseline["suite_snapshot"])
        results = harness.list_results(baseline["run_id"])
        assert (
            harness._compare_baseline(
                baseline["run_id"], baseline["summary"], list(reversed(results))
            )["comparable"]
            is True
        )
        assert (
            harness._compare_baseline(baseline["run_id"], baseline["summary"], results[:1])[
                "comparable"
            ]
            is False
        )
        assert (
            harness._compare_baseline(
                baseline["run_id"], baseline["summary"], [results[0], results[0]]
            )["comparable"]
            is False
        )
        trace = Path(results[0]["trace_bundle_path"])
        original = trace.read_text(encoding="utf-8")
        incomplete = json.loads(original)
        assert len(incomplete["events"]) > 2
        incomplete["events"].pop(1)
        assert trace_errors(incomplete, results[0]) == ["EVAL_TRACE_EVENT_SEQUENCE_INVALID"]
        truncated = json.loads(original)
        truncated["events"].pop()
        assert trace_errors(truncated, results[0]) == ["EVAL_TRACE_EVENT_SEQUENCE_INVALID"]
        bundle = json.loads(original)
        bundle["manifest"]["provider"]["model"] = "tampered"
        trace.write_text(json.dumps(bundle), encoding="utf-8")
        assert (
            harness._compare_baseline(baseline["run_id"], baseline["summary"], results)[
                "comparable"
            ]
            is False
        )
        trace.write_text(original, encoding="utf-8")
        result_file = (
            harness._run_path(baseline["run_id"]).parent / "results" / "audit_case__trial_002.json"
        )
        result_file.unlink()
        assert (
            harness._compare_baseline(baseline["run_id"], baseline["summary"], results)[
                "comparable"
            ]
            is False
        )
    finally:
        harness.shutdown()


def _slow_trial(case, trial_id, artifact_root, provider):
    marker = Path(artifact_root) / case["case_id"] / trial_id / "heartbeat.txt"
    while True:
        marker.write_text(str(time.monotonic()), encoding="utf-8")
        time.sleep(0.05)


@pytest.mark.parametrize("cancel", [False, True])
def test_trial_timeout_and_cancel_stop_real_process_writes(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr("app.evaluation.process._entry", _slow_trial)
    case = EvalCase(case_id="bounded_trial").model_copy(update={"timeout_seconds": 5})
    event = threading.Event()
    timer = threading.Timer(3, event.set) if cancel else None
    before = {child.pid for child in multiprocessing.active_children()}
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        result = run_eval_case(
            case=case, trial_id="trial_001", artifact_root=tmp_path / "eval", cancel_event=event
        )
    finally:
        if timer:
            timer.cancel()
            timer.join()
    assert time.monotonic() - started < 18
    assert result["terminal_state"] == ("cancelled" if cancel else "timed_out")
    assert result["expectation_met"] is False
    assert {child.pid for child in multiprocessing.active_children()} == before
    marker = tmp_path / "eval" / case.case_id / "trial_001" / "heartbeat.txt"
    assert marker.exists(), "The test must actually execute the child, not just time out its setup"
    last_write = marker.read_text()
    time.sleep(0.2)
    assert marker.read_text() == last_write


def test_harness_recovers_interrupted_runs_without_rerunning_them(app_paths):
    harness = EvaluationHarness(app_paths)
    definition = suite()
    harness.save_suite(definition)
    _, record = harness._prepare_run(definition.suite_id, None)
    harness.shutdown()
    recovered = EvaluationHarness(app_paths)
    try:
        result = recovered.get_run(record["run_id"])
        assert result["status"] == "failed"
        assert result["error"]["code"] == "EVAL_PROCESS_INTERRUPTED"
        assert recovered.list_results(record["run_id"]) == []
    finally:
        recovered.shutdown()


def test_other_harness_does_not_mark_live_owner_as_interrupted(app_paths):
    owner = EvaluationHarness(app_paths)
    owner.save_suite(suite())
    _, record = owner._prepare_run(suite().suite_id, None)
    observer = EvaluationHarness(app_paths)
    try:
        assert observer.get_run(record["run_id"])["status"] == "queued"
    finally:
        observer.shutdown()
        owner.shutdown()


def test_concurrent_suite_publication_never_overwrites_definition(app_paths):
    harnesses = [EvaluationHarness(app_paths), EvaluationHarness(app_paths)]
    barrier = threading.Barrier(2)

    def save(index):
        definition = suite().model_copy(update={"trials": index + 1})
        barrier.wait()
        try:
            return harnesses[index].save_suite(definition)
        except ValueError as exc:
            assert str(exc) == "EVAL_SUITE_IMMUTABLE"
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, [0, 1]))
        successful = [item for item in results if item is not None]
        assert len(successful) == 1
        assert harnesses[0].get_suite(suite().suite_id) == successful[0]
    finally:
        for harness in harnesses:
            harness.shutdown()
