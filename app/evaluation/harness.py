"""A small, local evaluation Harness built on the stable Target Adapter.

The Harness deliberately stores only safe result summaries and exported Trace
Bundles.  It is an independent evaluation surface: it does not become part of
the product Run graph and it never receives raw customer files.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import tempfile
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from app.core.paths import AppPaths

from .adapter import run_eval_case
from .contracts import EvalResult, EvalRun, EvalSuite
from app.governance.manifest import canonical_hash, compare_manifests
from .integrity import trace_errors, validate_identifier


HARNESS_SCHEMA = "risk-agent-eval-harness/v1"
_LIVE_HARNESSES: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


class EvaluationHarness:
    """Persistent local suite/run registry with one bounded worker thread."""

    def __init__(self, paths: AppPaths, *, max_workers: int = 1):
        self.paths = paths.ensure()
        self.root = self.paths.evaluations.resolve()
        self.suite_root = self.root / "suites"
        self.run_root = self.root / "runs"
        self.suite_root.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 2)))
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._closed = False
        self._owner_token = secrets.token_hex(16)
        _LIVE_HARNESSES[self._owner_token] = self
        # Harness runs cannot be resumed from an arbitrary partially executed trial.
        # Preserve all evidence and mark the interruption instead of showing a live run.
        for record in self.list_runs():
            if record.get("status") in {"queued", "running"} and not _owner_alive(record):
                record.update(
                    status="failed", finished_at=_now(), error={"code": "EVAL_PROCESS_INTERRUPTED"}
                )
                self._write(self._run_path(record["run_id"]), record)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)
        _LIVE_HARNESSES.pop(self._owner_token, None)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        record = self.get_run(run_id)
        with self._lock:
            event = self._cancel_events.get(run_id)
            if event is not None:
                event.set()
        return record

    def list_suites(self) -> list[dict[str, Any]]:
        return [
            self._read(path)
            for path in sorted(self.suite_root.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    def save_suite(self, suite: EvalSuite | dict[str, Any]) -> dict[str, Any]:
        parsed = suite if isinstance(suite, EvalSuite) else EvalSuite.model_validate(suite)
        payload = parsed.model_dump(mode="json")
        payload["suite_sha256"] = canonical_hash(payload)
        with self._lock:
            path = self._suite_path(parsed.suite_id)
            if path.exists():
                existing = self.get_suite(parsed.suite_id)
                if existing["suite_sha256"] != payload["suite_sha256"]:
                    raise ValueError("EVAL_SUITE_IMMUTABLE")
                return existing
            try:
                self._write(path, payload, exclusive=True)
            except FileExistsError:
                existing = self.get_suite(parsed.suite_id)
                if existing["suite_sha256"] != payload["suite_sha256"]:
                    raise ValueError("EVAL_SUITE_IMMUTABLE") from None
                return existing
        return payload

    def get_suite(self, suite_id: str) -> dict[str, Any]:
        path = self._suite_path(suite_id)
        if not path.is_file():
            raise KeyError(f"EVAL_SUITE_NOT_FOUND: {suite_id}")
        stored = self._read(path)
        payload = EvalSuite.model_validate(stored).model_dump(mode="json")
        digest = canonical_hash(payload)
        if stored.get("suite_sha256", digest) != digest:
            raise ValueError("EVAL_SUITE_CHECKSUM_MISMATCH")
        return {**payload, "suite_sha256": digest}

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
        with self._lock:
            suite, record = self._prepare_run(suite_id, baseline_run_id)
            self._futures[record["run_id"]] = self._executor.submit(
                self._execute, record["run_id"], suite, provider, baseline_run_id
            )
        return record

    def run_now(
        self,
        suite_id: str,
        *,
        provider: dict[str, Any] | None = None,
        baseline_run_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            suite, record = self._prepare_run(suite_id, baseline_run_id)
        return self._execute(record["run_id"], suite, provider, baseline_run_id)

    def _prepare_run(
        self, suite_id: str, baseline_run_id: str | None
    ) -> tuple[EvalSuite, dict[str, Any]]:
        if self._closed:
            raise ValueError("EVAL_HARNESS_CLOSED")
        suite = EvalSuite.model_validate(self.get_suite(suite_id))
        snapshot = suite.model_dump(mode="json")
        digest = canonical_hash(snapshot)
        if baseline_run_id:
            baseline = self.get_run(baseline_run_id)
            if baseline.get("suite_id") != suite_id:
                raise ValueError("EVAL_BASELINE_SUITE_MISMATCH")
            if baseline.get("status") != "completed":
                raise ValueError("EVAL_BASELINE_NOT_COMPLETED")
            if (
                baseline.get("suite_sha256") != digest
                or canonical_hash(baseline.get("suite_snapshot")) != digest
            ):
                raise ValueError("EVAL_BASELINE_SUITE_MISMATCH")
        run_id = f"eval_{secrets.token_hex(8)}"
        record = EvalRun(
            run_id=run_id,
            suite_id=suite.suite_id,
            suite_version=suite.version,
            suite_sha256=digest,
            suite_snapshot=snapshot,
            status="queued",
            started_at=_now(),
            baseline_run_id=baseline_run_id,
        ).model_dump(mode="json")
        record.update(
            owner_pid=os.getpid(),
            owner_started_at=psutil.Process().create_time(),
            owner_token=self._owner_token,
        )
        self._write(self._run_path(run_id), record)
        self._cancel_events[run_id] = threading.Event()
        return suite, record

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
                    if self._cancel_events[run_id].is_set():
                        raise InterruptedError("EVAL_CANCELLED")
                    trial_id = f"trial_{trial_number:03d}"
                    result = run_eval_case(
                        case=case,
                        trial_id=trial_id,
                        artifact_root=run_path.parent / "artifacts",
                        provider=provider,
                        cancel_event=self._cancel_events[run_id],
                    )
                    parsed = EvalResult.model_validate(result).model_dump(mode="json")
                    destination = result_dir / f"{case.case_id}__{trial_id}.json"
                    parsed.update(
                        category=case.category,
                        severity=case.severity,
                        case_config_sha256=canonical_hash(case.model_dump(mode="json")),
                    )
                    self._write(destination, parsed)
                    result_paths.append(str(destination))
                    results.append(parsed)
            if self._cancel_events[run_id].is_set():
                raise InterruptedError("EVAL_CANCELLED")
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
                    "status": "cancelled" if isinstance(exc, InterruptedError) else "failed",
                    "finished_at": _now(),
                    "result_paths": result_paths,
                    "error": {
                        "code": "EVAL_CANCELLED"
                        if isinstance(exc, InterruptedError)
                        else "EVAL_EXECUTION_FAILED",
                        "type": type(exc).__name__,
                    },
                }
            )
        self._write(run_path, record)
        with self._lock:
            self._futures.pop(run_id, None)
            self._cancel_events.pop(run_id, None)
        return record

    def _compare_baseline(
        self,
        baseline_run_id: str,
        candidate_summary: dict[str, Any],
        candidate_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline = self.get_run(baseline_run_id)
        baseline_results = self.list_results(baseline_run_id)
        expected_suite = EvalSuite.model_validate(baseline["suite_snapshot"])
        expected_keys = {
            (case.case_id, f"trial_{number:03d}")
            for case in expected_suite.cases
            for number in range(1, expected_suite.trials + 1)
        }
        left_index = {
            (item.get("case_id"), item.get("trial_id")): item for item in baseline_results
        }
        right_index = {
            (item.get("case_id"), item.get("trial_id")): item for item in candidate_results
        }
        paired = (
            len(left_index) == len(baseline_results)
            and len(right_index) == len(candidate_results)
            and set(left_index) == set(right_index) == expected_keys
        )
        manifest_comparisons: list[dict[str, Any]] = []
        for key in sorted(set(left_index) & set(right_index)):
            left, right = left_index[key], right_index[key]
            left_manifest = _load_manifest(left)
            right_manifest = _load_manifest(right)
            valid = not trace_errors(_load_bundle(left), left) and not trace_errors(
                _load_bundle(right), right
            )
            comparison = (
                compare_manifests(left_manifest, right_manifest)
                if valid and left_manifest is not None and right_manifest is not None
                else {
                    "comparable": False,
                    "differences": [],
                    "error": "EVAL_BASELINE_TRACE_INVALID",
                }
            )
            manifest_comparisons.append({"case_id": key[0], "trial_id": key[1], **comparison})
        baseline_summary = baseline.get("summary") or {}
        return {
            "schema_version": "risk-agent-eval-baseline-diff/v1",
            "baseline_run_id": baseline_run_id,
            "comparable": paired
            and bool(manifest_comparisons)
            and all(item["comparable"] for item in manifest_comparisons),
            "trial_keys_match": paired,
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
        path = self.suite_root / f"{suite_id}.json"
        if not path.resolve().is_relative_to(self.suite_root.resolve()):
            raise ValueError("EVAL_SUITE_PATH_INVALID")
        return path

    def _run_path(self, run_id: str) -> Path:
        _validate_identifier(run_id, "EVAL_RUN_ID_INVALID")
        path = self.run_root / run_id / "run.json"
        if not path.resolve().is_relative_to(self.run_root.resolve()):
            raise ValueError("EVAL_RUN_PATH_INVALID")
        return path

    @staticmethod
    def _write(path: Path, payload: dict[str, Any], *, exclusive: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".eval-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                payload, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            )
        try:
            if exclusive:
                os.link(temporary, path)
            else:
                temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

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
    case_index = {case.case_id: case for case in suite.cases}
    expected_keys = {
        (case.case_id, f"trial_{number:03d}")
        for case in suite.cases
        for number in range(1, suite.trials + 1)
    }
    actual_keys = [(item.get("case_id"), item.get("trial_id")) for item in results]
    expectations = [
        bool(item.get("expectation_met"))
        and not item.get("error")
        and item.get("terminal_state")
        == (
            case_index[item["case_id"]].expected_terminal_state
            if item.get("case_id") in case_index
            else None
        )
        for item in results
    ]
    met = sum(expectations)
    errors = sum(bool(item.get("error")) for item in results)
    security_events = sum(bool(item.get("security_events")) for item in results)
    traces = [_load_bundle(item) for item in results]
    integrity_errors = [
        trace_errors(
            bundle,
            {
                **item,
                "case_config_sha256": canonical_hash(
                    case_index[item["case_id"]].model_dump(mode="json")
                ),
            }
            if item.get("case_id") in case_index
            else item,
        )
        for item, bundle in zip(results, traces, strict=True)
    ]
    trace_complete = sum(not errors for errors in integrity_errors)
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
        "integrity": {
            "trial_keys_match": len(actual_keys) == len(set(actual_keys))
            and set(actual_keys) == expected_keys,
            "trace_valid": all(
                not errors or (bundle is None and not suite.gate.require_trace_for_each_case)
                for errors, bundle in zip(integrity_errors, traces, strict=True)
            ),
            "trace_errors": [
                {"case_id": item.get("case_id"), "trial_id": item.get("trial_id"), "codes": errors}
                for item, errors in zip(results, integrity_errors, strict=True)
                if errors
            ],
            "critical_cases_passed": all(
                matched
                for item, matched in zip(results, expectations, strict=True)
                if item.get("case_id") in case_index
                and case_index[item["case_id"]].severity in {"high", "critical"}
            ),
        },
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
    for name, passed in summary["integrity"].items():
        if name == "trace_errors":
            continue
        checks.append({"name": name, "actual": passed, "expected": True, "passed": passed is True})
    for name in ("raw_records_included", "hidden_chain_of_thought_included"):
        checks.append(
            {"name": name, "actual": risk[name], "expected": False, "passed": risk[name] is False}
        )
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
    validate_identifier(value, code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_alive(record: dict[str, Any]) -> bool:
    pid = record.get("owner_pid")
    if pid == os.getpid():
        owner = _LIVE_HARNESSES.get(record.get("owner_token"))
        return owner is not None and not owner._closed
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.create_time() == record.get("owner_started_at")
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, PermissionError):
        # Inability to inspect an owner is not proof it stopped.
        return True
