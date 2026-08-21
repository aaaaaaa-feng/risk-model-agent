from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.database import Database, new_id, now_iso
from app.core.security import sanitize_safe_evidence, sha256_bytes
from app.evaluation.manifest import verify_manifest


TRACE_SCHEMA = "risk-agent-trace/v1"
SPAN_KINDS = {"run", "agent", "llm", "tool", "reviewer", "gate"}
SPAN_STATUSES = {"requested", "running", "succeeded", "failed", "blocked", "cancelled"}
TRACE_STATUSES = SPAN_STATUSES


class TraceService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def new_run_rows(
        run_id: str,
        *,
        started_at: str,
        case_id: str | None = None,
        trial_id: str | None = None,
        manifest_hash: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        trace_id = new_id("trace")
        root_span_id = new_id("span")
        trace = {
            "id": trace_id,
            "run_id": run_id,
            "conversation_id": None,
            "case_id": case_id,
            "trial_id": trial_id,
            "status": "requested",
            "root_span_id": root_span_id,
            "metadata_json": {
                "schema_version": TRACE_SCHEMA,
                "manifest_hash": manifest_hash,
                "data_classification": "aggregate-and-hashes-only",
            },
            "started_at": started_at,
            "finished_at": None,
        }
        root = {
            "id": root_span_id,
            "trace_id": trace_id,
            "run_id": run_id,
            "parent_span_id": None,
            "kind": "run",
            "stage": "project_setup",
            "node": "start",
            "agent": "orchestrator",
            "tool": None,
            "status": "requested",
            "summary": "Risk modeling run",
            "input_hash": manifest_hash,
            "output_hash": "",
            "attempt": 1,
            "usage_json": {},
            "security_json": {
                "raw_data_recorded": False,
                "hidden_chain_of_thought_recorded": False,
            },
            "evidence_json": {"manifest_hash": manifest_hash},
            "started_at": started_at,
        }
        return trace, root

    def for_run(self, run_id: str) -> dict[str, Any]:
        traces = self.database.list(
            "traces", {"run_id": run_id}, order_by="started_at DESC", limit=1
        )
        if not traces:
            raise KeyError(f"TRACE_NOT_FOUND: {run_id}")
        return traces[0]

    def root_span_id(self, run_id: str) -> str:
        return str(self.for_run(run_id)["root_span_id"])

    def mark_running(self, run_id: str) -> None:
        trace = self.for_run(run_id)
        if trace["status"] == "requested":
            self.database.update("traces", trace["id"], {"status": "running"})
        root = self.database.get("trace_spans", trace["root_span_id"])
        if root and root["status"] == "requested":
            self.database.update("trace_spans", root["id"], {"status": "running"})

    def start_span(
        self,
        run_id: str,
        *,
        kind: str,
        stage: str,
        node: str,
        agent: str,
        tool: str | None = None,
        parent_span_id: str | None = None,
        summary: str = "",
        input_payload: Any = None,
        attempt: int = 1,
        retry_reason: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kind not in SPAN_KINDS - {"run"}:
            raise ValueError("TRACE_SPAN_KIND_INVALID")
        trace = self.for_run(run_id)
        parent = parent_span_id or str(trace["root_span_id"])
        parent_span = self.database.get("trace_spans", parent)
        if not parent_span:
            raise ValueError("TRACE_PARENT_SPAN_NOT_FOUND")
        if parent_span["trace_id"] != trace["id"]:
            raise ValueError("TRACE_PARENT_SPAN_CROSS_TRACE")
        return self.database.insert(
            "trace_spans",
            {
                "id": new_id("span"),
                "trace_id": trace["id"],
                "run_id": run_id,
                "parent_span_id": parent,
                "kind": kind,
                "stage": stage,
                "node": node,
                "agent": agent,
                "tool": tool,
                "status": "running",
                "summary": _safe_summary(summary),
                "input_hash": _payload_hash(input_payload),
                "output_hash": "",
                "attempt": max(1, int(attempt)),
                "retry_reason": retry_reason,
                "usage_json": {},
                "security_json": {},
                "evidence_json": _safe_trace_payload(evidence or {}),
                "started_at": now_iso(),
            },
        )

    def finish_span(
        self,
        span_id: str,
        status: str,
        *,
        output_payload: Any = None,
        error_code: str | None = None,
        error_type: str | None = None,
        degradation_path: str | None = None,
        usage: dict[str, Any] | None = None,
        security: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in SPAN_STATUSES - {"requested", "running"}:
            raise ValueError("TRACE_SPAN_TERMINAL_STATUS_INVALID")
        span = self.database.get("trace_spans", span_id)
        if not span:
            raise KeyError(span_id)
        if span["status"] in SPAN_STATUSES - {"requested", "running"}:
            return span
        finished_at = now_iso()
        duration = max(
            0,
            int(
                (_parse_time(finished_at) - _parse_time(span["started_at"])).total_seconds() * 1000
            ),
        )
        update = {
            "status": status,
            "output_hash": _payload_hash(output_payload),
            "error_code": error_code,
            "error_type": error_type,
            "degradation_path": degradation_path,
            "usage_json": _safe_trace_payload(usage or {}),
            "security_json": _safe_trace_payload(security or {}),
            "evidence_json": _safe_trace_payload(
                {**(span.get("evidence") or {}), **(evidence or {})}
            ),
            "finished_at": finished_at,
            "duration_ms": duration,
        }
        return self.database.update("trace_spans", span_id, update)

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        output_payload: Any = None,
        error_code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        if status not in TRACE_STATUSES - {"requested", "running"}:
            raise ValueError("TRACE_TERMINAL_STATUS_INVALID")
        trace = self.for_run(run_id)
        self.finish_span(
            str(trace["root_span_id"]),
            status,
            output_payload=output_payload,
            error_code=error_code,
            error_type=error_type,
        )
        self.database.update("traces", trace["id"], {"status": status, "finished_at": now_iso()})

    def bundle(self, run_id: str) -> dict[str, Any]:
        trace = self.for_run(run_id)
        run = self.database.get("runs", run_id)
        manifests = self.database.list("run_manifests", {"run_id": run_id}, limit=1)
        spans = self.database.list_all(
            "trace_spans", {"trace_id": trace["id"]}, order_by="started_at ASC"
        )
        events = self.database.list_all("events", {"run_id": run_id}, order_by="seq ASC")
        reviews = self.database.list_all("review_records", {"run_id": run_id}, order_by="round ASC")
        requests = self.database.list_all(
            "provider_requests", {"run_id": run_id}, order_by="created_at ASC"
        )
        decisions = self.database.list_all(
            "decisions", {"run_id": run_id}, order_by="created_at ASC"
        )
        artifacts = self.database.list_all(
            "artifacts", {"run_id": run_id}, order_by="created_at ASC"
        )
        manifest_payload = None
        if manifests:
            manifest_payload = verify_manifest(
                manifests[0].get("payload") or {}, str(manifests[0].get("manifest_hash") or "")
            )
        return {
            "schema_version": TRACE_SCHEMA,
            "trace": trace,
            "run": {
                key: (run or {}).get(key)
                for key in (
                    "id",
                    "project_id",
                    "target_task_id",
                    "status",
                    "stage",
                    "node",
                    "mode",
                    "error",
                    "started_at",
                    "finished_at",
                )
            },
            "manifest": manifest_payload,
            "spans": spans,
            "events": [
                {
                    "id": item["id"],
                    "sequence": item["seq"],
                    "stage": item["stage"],
                    "node": item["node"],
                    "agent": item["agent"],
                    "tool": item.get("tool"),
                    "status": item["status"],
                    "summary": _safe_summary(str(item.get("summary") or "")),
                    "evidence": _safe_trace_payload(item.get("evidence") or {}),
                    "created_at": item["created_at"],
                }
                for item in events
            ],
            "reviews": [
                {
                    "id": item["id"],
                    "round": item["round"],
                    "scope": item["scope"],
                    "status": item["status"],
                    "issue_codes": [
                        {
                            "code": issue.get("code"),
                            "severity": issue.get("severity"),
                        }
                        for issue in item.get("issues") or []
                    ],
                    "evidence": _safe_trace_payload(item.get("evidence") or {}),
                }
                for item in reviews
            ],
            "provider_requests": [
                {
                    "id": item["id"],
                    "provider": item["provider"],
                    "model": item["model"],
                    "status": item["status"],
                    "safe_payload_hash": item["safe_payload_hash"],
                    "usage": _safe_trace_payload(item.get("usage") or {}),
                    "response_summary": item["response_summary"],
                    "created_at": item["created_at"],
                }
                for item in requests
            ],
            "decisions": [
                {
                    "id": item["id"],
                    "stage": item["stage"],
                    "kind": item["kind"],
                    "status": item["status"],
                    "review_status": (item.get("review") or {}).get("status"),
                    "created_at": item["created_at"],
                    "resolved_at": item.get("resolved_at"),
                }
                for item in decisions
            ],
            "artifacts": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "checksum": item["checksum"],
                    "mime_type": item["mime_type"],
                }
                for item in artifacts
            ],
            "security_events": [
                {"span_id": item["id"], "security": item.get("security")}
                for item in spans
                if item.get("security")
            ],
            "hidden_chain_of_thought_included": False,
            "raw_records_included": False,
        }

    def export_bundle(self, run_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.bundle(run_id), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination


def _payload_hash(value: Any) -> str:
    if value is None:
        return ""
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    )


def _safe_trace_payload(value: Any) -> Any:
    try:
        return sanitize_safe_evidence(value)
    except ValueError:
        return {"redacted": True, "payload_sha256": _payload_hash(value)}


def _safe_summary(value: str) -> str:
    try:
        safe = sanitize_safe_evidence(value)
    except ValueError:
        return "敏感摘要已省略"
    return str(safe)[:500]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)
