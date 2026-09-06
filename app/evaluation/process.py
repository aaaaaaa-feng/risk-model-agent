"""Bounded process ownership for a complete Agent evaluation trial."""

from __future__ import annotations

import json
import multiprocessing
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from app.evaluation.contracts import EvalCase, EvalResult


def _entry(
    case: dict[str, Any], trial_id: str, artifact_root: str, provider: dict[str, Any] | None
) -> None:
    from app.evaluation.adapter import _run_eval_case_in_process

    _run_eval_case_in_process(
        case=case, trial_id=trial_id, artifact_root=Path(artifact_root), provider=provider
    )


def run_isolated_case(
    case: EvalCase,
    trial_id: str,
    artifact_root: Path,
    provider: dict[str, Any] | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    case_root = artifact_root / case.case_id / trial_id
    case_root.mkdir(parents=True, exist_ok=False)
    result_path = case_root / "exports" / "result.json"
    process = multiprocessing.get_context("spawn").Process(
        target=_entry,
        args=(case.model_dump(mode="json"), trial_id, str(artifact_root), provider),
        name=f"risk-eval-{case.case_id}-{trial_id}",
    )
    deadline = time.monotonic() + case.timeout_seconds
    terminal, code = "adapter_failed", "EVAL_PROCESS_FAILED"
    try:
        if cancel_event is None or not cancel_event.is_set():
            process.start()
            while process.is_alive():
                if cancel_event is not None and cancel_event.is_set():
                    terminal, code = "cancelled", "EVAL_CANCELLED"
                    break
                if time.monotonic() >= deadline:
                    terminal, code = "timed_out", "EVAL_TIMEOUT"
                    break
                process.join(0.1)
            else:
                if process.exitcode == 0 and result_path.is_file():
                    return EvalResult.model_validate_json(
                        result_path.read_text(encoding="utf-8")
                    ).model_dump(mode="json")
        else:
            terminal, code = "cancelled", "EVAL_CANCELLED"
    finally:
        if process.pid is not None:
            try:
                _terminate_tree(process)
            finally:
                if not process.is_alive():
                    process.close()
    result = EvalResult(
        case_id=case.case_id,
        trial_id=trial_id,
        terminal_state=terminal,
        expected_terminal_state=case.expected_terminal_state,
        expectation_met=False,
        final_response="评测已停止；不将未完成的工作视为通过。",
        error={
            "code": code,
            "type": "TimeoutError"
            if terminal == "timed_out"
            else "InterruptedError"
            if terminal == "cancelled"
            else "RuntimeError",
        },
    ).model_dump(mode="json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Cleanup happens only after the complete child process tree has stopped.
    if case.cleanup_workspace:
        shutil.rmtree(case_root / "workspace", ignore_errors=True)
    return result


def _terminate_tree(process: multiprocessing.Process) -> None:
    descendants: list[psutil.Process] = []
    inspection_failed = False
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        pass
    except (psutil.AccessDenied, PermissionError):
        inspection_failed = True
    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if process.is_alive():
        process.terminate()
    _, alive = psutil.wait_procs(descendants, timeout=2)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    process.join(2)
    if process.is_alive():
        process.kill()
        process.join(2)
    _, remaining = psutil.wait_procs(alive, timeout=2)
    if process.is_alive() or remaining:
        raise RuntimeError("EVAL_PROCESS_TERMINATION_FAILED")
    if inspection_failed:
        raise RuntimeError("EVAL_PROCESS_TREE_INSPECTION_FAILED")
