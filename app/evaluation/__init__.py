"""Stable, local-only evaluation integration boundary.

This package contains the stable adapter boundary and a small local Harness.
The Harness is intentionally file-backed and deterministic; it is not a
multi-user enterprise evaluation service.
"""

__all__ = [
    "MANIFEST_SCHEMA",
    "TRACE_SCHEMA",
    "TraceService",
    "compare_manifests",
    "run_eval_case",
    "EvaluationHarness",
]


def __getattr__(name: str):
    if name == "run_eval_case":
        from .adapter import run_eval_case

        return run_eval_case
    if name == "EvaluationHarness":
        from .harness import EvaluationHarness

        return EvaluationHarness
    if name in {"MANIFEST_SCHEMA", "compare_manifests"}:
        from app.governance.manifest import MANIFEST_SCHEMA, compare_manifests

        return {"MANIFEST_SCHEMA": MANIFEST_SCHEMA, "compare_manifests": compare_manifests}[name]
    if name in {"TRACE_SCHEMA", "TraceService"}:
        from app.governance.tracing import TRACE_SCHEMA, TraceService

        return {"TRACE_SCHEMA": TRACE_SCHEMA, "TraceService": TraceService}[name]
    raise AttributeError(name)
