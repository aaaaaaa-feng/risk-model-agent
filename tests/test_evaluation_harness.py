from __future__ import annotations

from pathlib import Path

from app.evaluation.contracts import EvalCase, EvalGate, EvalSuite
from app.evaluation.harness import EvaluationHarness


def test_harness_persists_safe_dimensions_and_gate(app_paths):
    harness = EvaluationHarness(app_paths)
    try:
        suite = EvalSuite(
            suite_id="harness_smoke_001",
            name="Harness smoke",
            cases=[
                EvalCase(
                    case_id="core_case_001",
                    category="core",
                    provider_profile="deterministic",
                    expected_terminal_state="succeeded",
                    rows=500,
                )
            ],
            gate=EvalGate(min_expectation_rate=1.0),
        )
        harness.save_suite(suite)
        result = harness.run_now(suite.suite_id)
        assert result["status"] == "completed"
        assert result["gate"]["passed"] is True
        assert result["summary"]["outcome"]["expectation_rate"] == 1.0
        assert result["summary"]["trajectory"]["trace_complete_rate"] == 1.0
        stored = harness.list_results(result["run_id"])
        assert len(stored) == 1
        trace = Path(stored[0]["trace_bundle_path"])
        assert trace.is_file()
        payload = trace.read_text(encoding="utf-8")
        assert "raw_records_included" in payload
        assert "stored_path" not in payload
    finally:
        harness.shutdown()


def test_harness_baseline_diff_reports_comparable_runs(app_paths):
    harness = EvaluationHarness(app_paths)
    try:
        suite = EvalSuite(
            suite_id="harness_compare_001",
            name="Harness compare",
            cases=[
                EvalCase(
                    case_id="core_case_002",
                    provider_profile="deterministic",
                    expected_terminal_state="succeeded",
                    rows=500,
                )
            ],
        )
        harness.save_suite(suite)
        baseline = harness.run_now(suite.suite_id)
        candidate = harness.run_now(suite.suite_id, baseline_run_id=baseline["run_id"])
        assert candidate["status"] == "completed"
        assert candidate["comparison"]["comparable"] is True
        assert candidate["gate"]["passed"] is True
    finally:
        harness.shutdown()
