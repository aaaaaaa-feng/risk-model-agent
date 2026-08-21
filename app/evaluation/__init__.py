"""Stable, local-only evaluation integration boundary.

This package is intentionally an adapter and contract layer, not an embedded
evaluation-management platform.
"""

__all__ = [
    "MANIFEST_SCHEMA",
    "TRACE_SCHEMA",
    "TraceService",
    "compare_manifests",
    "run_eval_case",
]


def __getattr__(name: str):
    if name == "run_eval_case":
        from .adapter import run_eval_case

        return run_eval_case
    if name in {"MANIFEST_SCHEMA", "compare_manifests"}:
        from .manifest import MANIFEST_SCHEMA, compare_manifests

        return {"MANIFEST_SCHEMA": MANIFEST_SCHEMA, "compare_manifests": compare_manifests}[name]
    if name in {"TRACE_SCHEMA", "TraceService"}:
        from .tracing import TRACE_SCHEMA, TraceService

        return {"TRACE_SCHEMA": TRACE_SCHEMA, "TraceService": TraceService}[name]
    raise AttributeError(name)
