"""A small, local evaluation Harness built on the stable Target Adapter.

The Harness deliberately stores only safe result summaries and exported Trace
Bundles.  It is an independent evaluation surface: it does not become part of
the product Run graph and it never receives raw customer files.
"""

from __future__ import annotations

import json
import secrets
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import AppPaths

from .adapter import run_eval_case
from .contracts import EvalResult, EvalRun, EvalSuite
from app.governance.manifest import compare_manifests


HARNESS_SCHEMA = "risk-agent-eval-harness/v1"


class EvaluationHarness:
    """Persistent local suite/run registry with one bounded worker thread."""

    def __init__(self, paths: AppPaths, *, max_workers: int = 1):
        self.paths = paths.ensure()
        self.root = self.paths.evaluations
        self.suite_root = self.root / "suites"
        self.run_root = self.root / "runs"
        self.suite_root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 2)))
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def list_suites(self) -> list[dict[str, Any]]:
        return [
            self._read(path)
            for path in sorted(self.suite_root.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    def save_suite(self, suite: EvalSuite | dict[str, Any]) -> dict[str, Any]:
        parsed = suite if isinstance(suite, EvalSuite) else EvalSuite.model_validate(suite)
        payload = parsed.model_dump(mode="json")
        self._write(self._suite_path(parsed.suite_id), payload)
        return payload

    def get_suite(self, suite_id: str) -> dict[str, Any]:
        path = self._suite_path(suite_id)
        if not path.is_file():
            raise KeyError(f"EVAL_SUITE_NOT_FOUND: {suite_id}")
        return self._read(path)

    def list_runs(self, suite_id: str | None = None) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.run_root.glob("*/run.json")):
            if not path.is_file() or path.is_symlink():
                continue
            item = self._read(path)
            if suite_id is None or item.get("suite_id") == suite_id:
                values.append(item)
        return sorted(values, key=lambda item: str(item.get("started_at") or ""), reverse=True)

    def get_run(self, run_id: str) -> dict[str, Any]:
        path = self._run_path(run_id)
        if not path.is_file():
            raise KeyError(f"EVAL_RUN_NOT_FOUND: {run_id}")
        return self._read(path)

    def list_results(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        directory = self._run_path(run_id).parent / "results"
        return [
            self._read(path)
            for path in sorted(directory.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    def start_run(
        self,
        suite_id: str,
        *,
        provider: dict[str, Any] | None = None,
        baseline_run_id: str | None = None,
    ) -> dict[str, Any]:
        suite = EvalSuite.model_validate(self.get_suite(suite_id))
        if baseline_run_id:
            baseline = self.get_run(baseline_run_id)
            if baseline.get("suite_id") != suite_id:
                raise ValueError("EVAL_BASELINE_SUITE_MISMATCH")
        run_id = f"eval_{secrets.token_hex(8)}"
        started = _now()
        record = EvalRun(
            run_id=run_id,
            suite_id=suite.suite_id,
            suite_version=suite.version,
            status="queued",
            started_at=started,
            baseline_run_id=baseline_run_id,
        ).model_dump(mode="json")
        self._write(self._run_path(run_id), record)
        with self._lock:
            self._futures[run_id] = self._executor.submit(
                self._execute, run_id, suite, provider, baseline_run_id
            )
        return record

    def run_now(
        self,
        suite_id: str,
        *,
        provider: dict[str, Any] | None = None,
        baseline_run_id: str | None = None,
    ) -> dict[str, Any]:
        suite = EvalSuite.model_validate(self.get_suite(suite_id))
        if baseline_run_id:
            baseline = self.get_run(baseline_run_id)
            if baseline.get("suite_id") != suite_id:
                raise ValueError("EVAL_BASELINE_SUITE_MISMATCH")
        run_id = f"eval_{secrets.token_hex(8)}"
        record = EvalRun(
            run_id=run_id,
            suite_id=suite.suite_id,
            suite_version=suite.version,
            status="queued",
            started_at=_now(),
            baseline_run_id=baseline_run_id,
        ).model_dump(mode="json")
        self._write(self._run_path(run_id), record)
        return self._execute(run_id, suite, provider, baseline_run_id)

    def _execute(
        self,
        run_id: str,
        suite: EvalSuite,
        provider: dict[str, Any] | None,
        baseline_run_id: str | None,
    ) -> dict[str, Any]:
        run_path = self._run_path(run_id)
        record = self.get_run(run_id)
        record.update({"status": "running", "started_at": _now(), "error": None})
        self._write(run_path, record)
        result_dir = run_path.parent / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_paths: list[str] = []
        results: list[dict[str, Any]] = []
        try:
            for case in suite.cases:
                for trial_number in range(1, suite.trials + 1):
                    trial_id = f"trial_{trial_number:03d}"
                    result = run_eval_case(
                        case=case,
                        trial_id=trial_id,
                        artifact_root=run_path.parent / "artifacts",
                        provider=provider,
                    )
                    parsed = EvalResult.model_validate(result).model_dump(mode="json")
                    destination = result_dir / f"{case.case_id}__{trial_id}.json"
                    self._write(destination, parsed)
                    result_paths.append(str(destination))
                    results.append({**parsed, "category": case.category, "severity": case.severity})
            summary = _summarize(results, suite)
            gate = _evaluate_gate(summary, suite)
            comparison = None
            if baseline_run_id:
                comparison = self._compare_baseline(baseline_run_id, summary, results)
                gate["checks"].append(
                    {
                        "name": "baseline_comparable",
                        "actual": comparison["comparable"],
                        "expected": True,
                        "passed": comparison["comparable"],
                    }
                )
                gate["passed"] = bool(gate["passed"] and comparison["comparable"])
            record.update(
                {
                    "status": "completed",
                    "finished_at": _now(),
                    "result_paths": result_paths,
                    "summary": summary,
                    "gate": gate,
                    "comparison": comparison,
                }
            )
        except Exception as exc:
            record.update(
                {
                    "status": "failed",
                    "finished_at": _now(),
                    "result_paths": result_paths,
                    "error": {"code": str(exc).split(":", 1)[0], "type": type(exc).__name__},
                }
            )
        self._write(run_path, record)
        with self._lock:
            self._futures.pop(run_id, None)
        return record

    def _compare_baseline(
        self,
        baseline_run_id: str,
        candidate_summary: dict[str, Any],
        candidate_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline = self.get_run(baseline_run_id)
        baseline_results = [self._read(Path(path)) for path in baseline.get("result_paths") or []]
        manifest_comparisons: list[dict[str, Any]] = []
        for left, right in zip(baseline_results, candidate_results, strict=False):
            left_manifest = _load_manifest(left)
            right_manifest = _load_manifest(right)
            if left_manifest is not None and right_manifest is not None:
                manifest_comparisons.append(compare_manifests(left_manifest, right_manifest))
        baseline_summary = baseline.get("summary") or {}
        return {
            "schema_version": "risk-agent-eval-baseline-diff/v1",
            "baseline_run_id": baseline_run_id,
            "comparable": bool(manifest_comparisons)
            and all(item["comparable"] for item in manifest_comparisons),
            "expectation_rate_delta": _delta(
                candidate_summary.get("outcome", {}).get("expectation_rate"),
                baseline_summary.get("outcome", {}).get("expectation_rate"),
            ),
            "error_rate_delta": _delta(
                candidate_summary.get("outcome", {}).get("error_rate"),
                baseline_summary.get("outcome", {}).get("error_rate"),
            ),
            "manifest_comparisons": manifest_comparisons,
        }

    def _suite_path(self, suite_id: str) -> Path:
        _validate_identifier(suite_id, "EVAL_SUITE_ID_INVALID")
        return self.suite_root / f"{suite_id}.json"

    def _run_path(self, run_id: str) -> Path:
        _validate_identifier(run_id, "EVAL_RUN_ID_INVALID")
        return self.run_root / run_id / "run.json"

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(path)

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise ValueError("EVAL_RECORD_INVALID") from exc
        if not isinstance(value, dict):
            raise ValueError("EVAL_RECORD_INVALID")
        return value


def _summarize(results: list[dict[str, Any]], suite: EvalSuite) -> dict[str, Any]:
    total = len(results)
    if not total:
        raise ValueError("EVAL_SUITE_EMPTY")
    met = sum(bool(item.get("expectation_met")) for item in results)
    errors = sum(bool(item.get("error")) for item in results)
    security_events = sum(bool(item.get("security_events")) for item in results)
    traces = [_load_bundle(item) for item in results]
    trace_complete = sum(_trace_is_complete(bundle) for bundle in traces if bundle is not None)
    trace_count = sum(bundle is not None for bundle in traces)
    tokens = [int((item.get("usage") or {}).get("total_tokens") or 0) for item in results]
    durations = [_trace_duration(bundle) for bundle in traces if bundle is not None]
    by_category: dict[str, dict[str, Any]] = {}
    for category in {str(item.get("category") or "core") for item in results}:
        subset = [item for item in results if item.get("category") == category]
        by_category[category] = {
            "cases": len(subset),
            "expectation_rate": _ratio(
                sum(bool(item.get("expectation_met")) for item in subset), len(subset)
            ),
            "error_rate": _ratio(sum(bool(item.get("error")) for item in subset), len(subset)),
        }
    return {
        "schema_version": HARNESS_SCHEMA,
        "suite_id": suite.suite_id,
        "suite_version": suite.version,
        "cases": total,
        "trials": suite.trials,
        "outcome": {
            "expectation_rate": _ratio(met, total),
            "error_rate": _ratio(errors, total),
            "met": met,
        },
        "trajectory": {
            "trace_available_rate": _ratio(trace_count, total),
            "trace_complete_rate": _ratio(trace_complete, total),
            "average_spans": _average(
                [len(bundle.get("spans") or []) for bundle in traces if bundle]
            ),
            "average_events": _average(
                [len(bundle.get("events") or []) for bundle in traces if bundle]
            ),
        },
        "efficiency": {
            "average_duration_ms": _average(durations),
            "average_total_tokens": _average(tokens),
            "average_provider_requests": _average(
                [
                    int((item.get("usage") or {}).get("provider_request_count") or 0)
                    for item in results
                ]
            ),
        },
        "risk": {
            "security_event_rate": _ratio(security_events, total),
            "raw_records_included": any(
                bool(bundle and bundle.get("raw_records_included")) for bundle in traces
            ),
            "hidden_chain_of_thought_included": any(
                bool(bundle and bundle.get("hidden_chain_of_thought_included")) for bundle in traces
            ),
        },
        "by_category": by_category,
    }


def _evaluate_gate(summary: dict[str, Any], suite: EvalSuite) -> dict[str, Any]:
    gate = suite.gate
    outcome = summary["outcome"]
    trajectory = summary["trajectory"]
    efficiency = summary["efficiency"]
    risk = summary["risk"]
    checks = [
        {
            "name": "expectation_rate",
            "actual": outcome["expectation_rate"],
            "expected": f">={gate.min_expectation_rate}",
            "passed": outcome["expectation_rate"] >= gate.min_expectation_rate,
        },
        {
            "name": "error_rate",
            "actual": outcome["error_rate"],
            "expected": f"<={gate.max_error_rate}",
            "passed": outcome["error_rate"] <= gate.max_error_rate,
        },
        {
            "name": "security_event_rate",
            "actual": risk["security_event_rate"],
            "expected": f"<={gate.max_security_event_rate}",
            "passed": risk["security_event_rate"] <= gate.max_security_event_rate,
        },
    ]
    if gate.require_trace_for_each_case:
        checks.append(
            {
                "name": "trace_complete_rate",
                "actual": trajectory["trace_complete_rate"],
                "expected": 1.0,
                "passed": trajectory["trace_complete_rate"] >= 1.0,
            }
        )
    if gate.max_average_tokens is not None:
        checks.append(
            {
                "name": "average_total_tokens",
                "actual": efficiency["average_total_tokens"],
                "expected": f"<={gate.max_average_tokens}",
                "passed": efficiency["average_total_tokens"] <= gate.max_average_tokens,
            }
        )
    return {
        "schema_version": "risk-agent-eval-gate/v1",
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def _load_bundle(result: dict[str, Any]) -> dict[str, Any] | None:
    value = result.get("trace_bundle_path")
    if not value:
        return None
    path = Path(str(value))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_manifest(result: dict[str, Any]) -> dict[str, Any] | None:
    bundle = _load_bundle(result)
    value = bundle.get("manifest") if bundle else None
    return value if isinstance(value, dict) else None


def _trace_is_complete(bundle: dict[str, Any] | None) -> bool:
    if not bundle:
        return False
    trace = bundle.get("trace") or {}
    spans = bundle.get("spans") or []
    return bool(
        trace.get("id") and trace.get("root_span_id") and spans and bundle.get("events") is not None
    )


def _trace_duration(bundle: dict[str, Any] | None) -> float:
    if not bundle:
        return 0.0
    root_id = (bundle.get("trace") or {}).get("root_span_id")
    for span in bundle.get("spans") or []:
        if span.get("id") == root_id:
            return float(span.get("duration_ms") or 0)
    return 0.0


def _ratio(value: int, total: int) -> float:
    return round(value / total, 6) if total else 0.0


def _average(values: list[float | int]) -> float:
    return round(sum(float(value) for value in values) / len(values), 4) if values else 0.0


def _delta(candidate: Any, baseline: Any) -> float | None:
    if candidate is None or baseline is None:
        return None
    return round(float(candidate) - float(baseline), 6)


def _validate_identifier(value: str, code: str) -> None:
    if (
        not value
        or len(value) > 120
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in value
        )
    ):
        raise ValueError(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
