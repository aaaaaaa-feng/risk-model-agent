"""Independent validation of persisted evaluation evidence before a release gate."""

from __future__ import annotations

import math
import re
from typing import Any

from app.core.security import validate_provider_text, validate_safe_evidence
from app.governance.manifest import (
    COMPARABILITY_FIELDS,
    MANIFEST_SCHEMA,
    canonical_hash,
    verify_manifest,
)
from app.governance.tracing import TRACE_SCHEMA


def validate_identifier(value: str, code: str) -> str:
    # One portable path component, including on Windows (no dot aliases/devices).
    if (
        not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}", value)
        or value.endswith(".")
        or value.split(".", 1)[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise ValueError(code)
    return value


def trace_errors(bundle: dict[str, Any] | None, result: dict[str, Any]) -> list[str]:
    if bundle is None:
        return ["EVAL_TRACE_MISSING"]
    try:
        _validate_trace(bundle, result)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        # Never copy malformed evidence or a filesystem path to the gate record.
        code = str(exc).split(":", 1)[0]
        return [code if re.fullmatch(r"[A-Z][A-Z0-9_]+", code) else "EVAL_TRACE_INVALID"]
    return []


def _validate_trace(bundle: dict[str, Any], result: dict[str, Any]) -> None:
    if bundle.get("schema_version") != TRACE_SCHEMA:
        raise ValueError("EVAL_TRACE_SCHEMA_INVALID")
    if any(
        bundle.get(key) is not False
        for key in ("raw_records_included", "hidden_chain_of_thought_included")
    ):
        raise ValueError("EVAL_TRACE_DISCLOSURE_FORBIDDEN")
    _scan_content(bundle)
    manifest = bundle["manifest"]
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("EVAL_MANIFEST_SCHEMA_INVALID")
    verify_manifest(manifest, manifest.get("manifest_sha256", ""))
    for field in COMPARABILITY_FIELDS:
        value = manifest
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value or value[part] is None:
                raise ValueError("EVAL_MANIFEST_FIELDS_MISSING")
            value = value[part]
    policy = manifest["policy"]
    if (
        policy.get("raw_data_provider_egress") is not False
        or policy.get("hidden_chain_of_thought_recorded") is not False
        or canonical_hash(policy) != manifest["policy_hash"]
    ):
        raise ValueError("EVAL_MANIFEST_POLICY_INVALID")
    trace, run, spans = bundle["trace"], bundle["run"], bundle["spans"]
    run_id = result.get("run_id")
    context = manifest["eval_suite"]
    if not run_id or any(
        value != run_id for value in (run["id"], trace["run_id"], manifest["run_id"])
    ):
        raise ValueError("EVAL_TRACE_RUN_MISMATCH")
    for key in ("case_id", "trial_id"):
        if context.get(key) != result.get(key) or trace.get(key) != result.get(key):
            raise ValueError("EVAL_TRACE_CASE_MISMATCH")
    if (
        result.get("case_config_sha256")
        and context.get("case_config_sha256") != result["case_config_sha256"]
    ):
        raise ValueError("EVAL_TRACE_CASE_CONFIG_MISMATCH")
    if (trace.get("metadata") or {}).get("manifest_hash") != manifest["manifest_sha256"]:
        raise ValueError("EVAL_TRACE_MANIFEST_MISMATCH")
    terminal = result.get("terminal_state")
    if (
        terminal not in {"succeeded", "failed", "blocked"}
        or run["status"] != terminal
        or trace["status"] != terminal
    ):
        raise ValueError("EVAL_TRACE_TERMINAL_MISMATCH")
    if not run.get("finished_at") or not trace.get("finished_at"):
        raise ValueError("EVAL_TRACE_UNFINISHED")
    if not isinstance(spans, list) or not spans or not isinstance(bundle["events"], list):
        raise ValueError("EVAL_TRACE_STRUCTURE_INVALID")
    indexed = {span["id"]: span for span in spans}
    root = trace["root_span_id"]
    if (
        len(indexed) != len(spans)
        or root not in indexed
        or indexed[root].get("parent_span_id") is not None
    ):
        raise ValueError("EVAL_TRACE_ROOT_INVALID")
    if indexed[root]["kind"] != "run" or indexed[root]["status"] != terminal:
        raise ValueError("EVAL_TRACE_ROOT_INVALID")
    for span in spans:
        if span["trace_id"] != trace["id"] or span["run_id"] != run_id:
            raise ValueError("EVAL_TRACE_SPAN_LINEAGE_INVALID")
        if span["status"] not in {"succeeded", "failed", "blocked", "cancelled"} or not span.get(
            "finished_at"
        ):
            raise ValueError("EVAL_TRACE_UNFINISHED")
        seen: set[str] = set()
        cursor = span["id"]
        while cursor != root:
            if cursor in seen or cursor not in indexed:
                raise ValueError("EVAL_TRACE_SPAN_LINEAGE_INVALID")
            seen.add(cursor)
            cursor = indexed[cursor].get("parent_span_id")
        for key in ("evidence", "usage", "security"):
            validate_safe_evidence(span.get(key) or {})
    sequences = [event["sequence"] for event in bundle["events"]]
    if not sequences or sequences != sorted(set(sequences)):
        raise ValueError("EVAL_TRACE_EVENT_SEQUENCE_INVALID")
    for event in bundle["events"]:
        evidence = event.get("evidence") or {}
        validate_safe_evidence(evidence)
        if evidence.get("span_id") and evidence["span_id"] not in indexed:
            raise ValueError("EVAL_TRACE_EVENT_LINEAGE_INVALID")
        if evidence.get("trace_id") and evidence["trace_id"] != trace["id"]:
            raise ValueError("EVAL_TRACE_EVENT_LINEAGE_INVALID")


def _scan_content(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if (
                lowered.startswith("raw_")
                or lowered
                in {
                    "records",
                    "source_path",
                    "stored_path",
                    "sample_values",
                    "chain_of_thought",
                    "hidden_chain_of_thought",
                    "reasoning_content",
                }
            ) and item is not False:
                raise ValueError("EVAL_TRACE_DISCLOSURE_FORBIDDEN")
            _scan_content(item)
    elif isinstance(value, list):
        for item in value:
            _scan_content(item)
    elif isinstance(value, str):
        validate_provider_text(value)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("EVAL_TRACE_NONFINITE_VALUE")
