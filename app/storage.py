from __future__ import annotations

import hashlib
import json
import math
import re
import numbers
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, RUNTIME_DIR, ensure_runtime, new_id


DB_PATH = RUNTIME_DIR / "risk_agent.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Make persisted/API payloads strict-JSON compatible.

    Model libraries occasionally expose NaN or infinity in parameter metadata (for
    example XGBoost's optional defaults). Those values are valid Python floats but
    are rejected by strict JSON responses, so represent them as an explicit null.
    """
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                return None
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return _json_safe(value.tolist())
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


_TRACE_SECRET_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}
_TRACE_RAW_KEYS = {
    "raw",
    "raw_data",
    "raw_rows",
    "rows_data",
    "customer_rows",
}


def _trace_redact(value: Any, key: str = "") -> Any:
    """Redact credentials, local paths and customer-level payloads in traces.

    Trace bundles are intended for later evaluation and debugging, not for
    moving the source dataset out of the local runtime. Keep aggregate state
    and event hashes while failing closed on fields that could contain secrets
    or row-level data.
    """
    normalized = key.lower().replace("-", "_")
    if normalized in _TRACE_SECRET_KEYS or any(marker in normalized for marker in ("api_key", "secret", "password", "authorization")):
        return "<redacted>"
    if normalized in _TRACE_RAW_KEYS or any(marker in normalized for marker in ("raw_rows", "raw_data", "customer_rows")):
        return {"included": False} if isinstance(value, (dict, list)) else False
    if normalized == "path" or normalized.endswith("_path"):
        return "<local-path-redacted>"
    if isinstance(value, dict):
        return {str(item_key): _trace_redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_redact(item, key) for item in value]
    return _json_safe(value)


def _trace_alias(value: Any, aliases: Dict[str, str]) -> Any:
    """Replace original field names in trace text and keys with stable aliases."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            aliased_key = aliases.get(key_text, key_text)
            result[aliased_key] = _trace_alias(item, aliases)
        return result
    if isinstance(value, list):
        return [_trace_alias(item, aliases) for item in value]
    if isinstance(value, str):
        result = value
        for original, alias in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            result = re.sub(re.escape(original), alias, result)
        return result
    return value


class Store:
    def __init__(self, db_path: Path = DB_PATH):
        ensure_runtime()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    rows INTEGER,
                    columns INTEGER,
                    sheet TEXT,
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    profile_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    event_id TEXT,
                    reaction TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_usage (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    tokens INTEGER NOT NULL,
                    model TEXT,
                    purpose TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_requests (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    purpose TEXT,
                    model TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_dictionaries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    rows INTEGER,
                    columns INTEGER,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Keep existing local databases forward-compatible after the demo label
            # was introduced. Runtime data is local and disposable, but migrations
            # must still preserve the user's prior projects.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(datasets)").fetchall()}
            if "is_demo" not in columns:
                conn.execute("ALTER TABLE datasets ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
            dictionary_columns = {row[1] for row in conn.execute("PRAGMA table_info(data_dictionaries)").fetchall()}
            if "metadata_json" not in dictionary_columns:
                conn.execute("ALTER TABLE data_dictionaries ADD COLUMN metadata_json TEXT")

    def create_project(self, name: str) -> Dict[str, Any]:
        project_id = new_id("proj")
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO projects(id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (project_id, name.strip() or "未命名风控项目", "draft", timestamp, timestamp),
            )
        self.project_dir(project_id).mkdir(parents=True, exist_ok=True)
        return self.get_project(project_id)

    def project_dir(self, project_id: str) -> Path:
        path = (DATA_DIR / project_id).resolve()
        if DATA_DIR.resolve() not in path.parents:
            raise ValueError("project path escaped data root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def update_project_status(self, project_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET status=?,updated_at=? WHERE id=?",
                (status, now_iso(), project_id),
            )

    def create_dataset(
        self,
        project_id: str,
        filename: str,
        path: Path,
        sha256: str,
        bytes_count: int,
        rows: Optional[int] = None,
        columns: Optional[int] = None,
        sheet: Optional[str] = None,
        is_demo: bool = False,
    ) -> Dict[str, Any]:
        dataset_id = new_id("data")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO datasets
                (id,project_id,filename,path,sha256,bytes,rows,columns,sheet,is_demo,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    dataset_id,
                    project_id,
                    filename,
                    str(path),
                    sha256,
                    bytes_count,
                    rows,
                    columns,
                    sheet,
                    int(is_demo),
                    now_iso(),
                ),
            )
        self.update_project_status(project_id, "data_imported")
        return self.get_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["profile"] = loads(result.pop("profile_json", None), {})
        result["is_demo"] = bool(result.get("is_demo"))
        return result

    def list_datasets(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM datasets WHERE project_id=? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["profile"] = loads(item.pop("profile_json", None), {})
            item["is_demo"] = bool(item.get("is_demo"))
            result.append(item)
        return result

    def create_dictionary(
        self,
        project_id: str,
        filename: str,
        path: Path,
        sha256: str,
        rows: Optional[int],
        columns: Optional[int],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        dictionary_id = new_id("dict")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO data_dictionaries(id,project_id,filename,path,sha256,rows,columns,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (dictionary_id, project_id, filename, str(path), sha256, rows, columns, dumps(metadata or {}), now_iso()),
            )
        return self.get_dictionary(dictionary_id) or {}

    def get_dictionary(self, dictionary_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM data_dictionaries WHERE id=?", (dictionary_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = loads(result.pop("metadata_json", None), {})
        return result

    def list_dictionaries(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM data_dictionaries WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = loads(item.pop("metadata_json", None), {})
            result.append(item)
        return result

    def latest_dictionary(self, project_id: str) -> Optional[Dict[str, Any]]:
        dictionaries = self.list_dictionaries(project_id)
        return dictionaries[0] if dictionaries else None

    def list_decisions(self, run_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        return [{**dict(row), "payload": loads(row["payload_json"], {})} for row in rows]

    def list_feedback(self, run_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM feedback WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_provider_usage(self, run_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,run_id,tokens,model,purpose,created_at FROM provider_usage WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_provider_request(
        self,
        run_id: str,
        purpose: str,
        model: str,
        payload: Dict[str, Any],
        status: str = "sent",
        error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_json = dumps(payload)
        request = {
            "id": new_id("provider_req"),
            "run_id": run_id,
            "purpose": purpose,
            "model": model,
            "payload": _trace_redact(payload),
            "payload_hash": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            "policy_version": "provider-redaction/v1",
            "status": status,
            "error_code": error_code,
            "created_at": now_iso(),
        }
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO provider_requests(id,run_id,purpose,model,payload_json,payload_hash,policy_version,status,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (request["id"], run_id, purpose, model, dumps(request["payload"]), request["payload_hash"], request["policy_version"], status, error_code, request["created_at"]),
            )
        return request

    def list_provider_requests(self, run_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,run_id,purpose,model,payload_json,payload_hash,policy_version,status,error_code,created_at FROM provider_requests WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in ("id", "run_id", "purpose", "model", "payload_hash", "policy_version", "status", "error_code", "created_at")},
                "payload": loads(row["payload_json"], {}),
            }
            for row in rows
        ]

    def trace_bundle(self, run_id: str) -> Dict[str, Any]:
        """Return an evaluation-ready, local-only trace projection for a Run."""
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        run_view = {
            key: run.get(key)
            for key in (
                "id",
                "project_id",
                "dataset_id",
                "status",
                "phase",
                "mode",
                "error",
                "created_at",
                "updated_at",
            )
        }
        # The full state is useful for future evaluation (node decisions,
        # screening funnel, reviews), but is recursively redacted first.
        raw_trace = _trace_redact(
            {
                "schema_version": "risk-trace-bundle/v1",
                "run": run_view,
                "state": run.get("state") or {},
                "events": self.list_events(run_id),
                "decisions": self.list_decisions(run_id),
                "feedback": self.list_feedback(run_id),
                "provider_usage": self.list_provider_usage(run_id),
                "provider_requests": self.list_provider_requests(run_id),
                "manifest": {
                    "raw_data_included": False,
                    "raw_rows_included": False,
                    "credentials_included": False,
                    "local_paths_included": False,
                    "event_chain_verified_by": "event_hash + previous_hash",
                    "event_chain": self.verify_event_chain(run_id),
                    "intended_use": "future evaluation harness input; not a production approval record",
                },
            }
        )
        profile_columns = ((run.get("state") or {}).get("profile") or {}).get("columns_detail") or []
        aliases = {
            str(item.get("name")): f"f_{index:04d}"
            for index, item in enumerate(profile_columns, start=1)
            if item.get("name")
        }
        trace = _trace_alias(raw_trace, aliases)
        trace["manifest"]["original_column_names_included"] = False
        trace["manifest"]["field_alias_policy"] = "stable f_#### aliases"
        return trace

    def project_snapshot(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        runs = self.list_runs(project_id)
        return {
            "schema_version": "risk-project-backup/v1",
            "project": project,
            "datasets": [{key: value for key, value in item.items() if key != "path"} for item in self.list_datasets(project_id)],
            "dictionaries": [{key: value for key, value in item.items() if key != "path"} for item in self.list_dictionaries(project_id)],
            "runs": [
                {
                    **{key: value for key, value in run.items() if key != "state"},
                    "state": run.get("state", {}),
                    "events": self.list_events(run["id"]),
                    "decisions": self.list_decisions(run["id"]),
                    "feedback": self.list_feedback(run["id"]),
                }
                for run in runs
            ],
            "raw_data_included": False,
        }

    def update_dataset_profile(self, dataset_id: str, profile: Dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE datasets SET rows=?,columns=?,profile_json=? WHERE id=?",
                (
                    profile.get("rows"),
                    profile.get("columns"),
                    dumps(profile),
                    dataset_id,
                ),
            )

    def create_run(
        self,
        project_id: str,
        dataset_id: str,
        mode: str,
        initial_state: Optional[Dict[str, Any]] = None,
        phase: str = "profiling",
    ) -> Dict[str, Any]:
        run_id = new_id("run")
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO runs
                (id,project_id,dataset_id,status,phase,mode,state_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id, project_id, dataset_id, "queued", phase, mode, dumps(initial_state or {}), timestamp, timestamp),
            )
        self.update_project_status(project_id, "active")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["state"] = loads(result.pop("state_json", None), {})
        return result

    def list_runs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["state"] = loads(item.pop("state_json", None), {})
            result.append(item)
        return result

    def update_run(
        self,
        run_id: str,
        status: Optional[str] = None,
        phase: Optional[str] = None,
        state: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        current = self.get_run(run_id)
        if not current:
            raise KeyError(run_id)
        with self.connect() as conn:
            conn.execute(
                """UPDATE runs SET status=?,phase=?,state_json=?,error=?,updated_at=? WHERE id=?""",
                (
                    status or current["status"],
                    phase or current["phase"],
                    dumps(state if state is not None else current["state"]),
                    error,
                    now_iso(),
                    run_id,
                ),
            )

    def update_run_dataset(self, run_id: str, dataset_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE runs SET dataset_id=?,updated_at=? WHERE id=?", (dataset_id, now_iso(), run_id))

    def append_event(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT sequence,event_hash FROM events WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            sequence = (int(row["sequence"]) + 1) if row else 1
            previous_hash = row["event_hash"] if row else ""
            created_at = now_iso()
            canonical = dumps(
                {
                    "run_id": run_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "payload": payload,
                    "created_at": created_at,
                    "previous_hash": previous_hash,
                }
            )
            event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO events(run_id,sequence,event_type,payload_json,previous_hash,event_hash,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (run_id, sequence, event_type, dumps(payload), previous_hash, event_hash, created_at),
            )
        return {
            "run_id": run_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        }

    def list_events(self, run_id: str, after: int = 0) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (run_id, after),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "payload": loads(row["payload_json"], {}),
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def verify_event_chain(self, run_id: str) -> Dict[str, Any]:
        events = self.list_events(run_id)
        previous_hash = ""
        for event in events:
            canonical = dumps(
                {
                    "run_id": run_id,
                    "sequence": event["sequence"],
                    "event_type": event["event_type"],
                    "payload": event["payload"],
                    "created_at": event["created_at"],
                    "previous_hash": previous_hash,
                }
            )
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if event.get("previous_hash") != previous_hash or event.get("event_hash") != expected:
                return {"valid": False, "event_count": len(events), "failed_sequence": event.get("sequence")}
            previous_hash = expected
        return {"valid": True, "event_count": len(events), "last_hash": previous_hash}

    def add_decision(self, run_id: str, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        decision = {"id": new_id("decision"), "run_id": run_id, "kind": kind, "payload": payload, "created_at": now_iso()}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO decisions(id,run_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (decision["id"], run_id, kind, dumps(payload), decision["created_at"]),
            )
        return decision

    def add_feedback(self, run_id: str, reaction: str, reason: Optional[str], event_id: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO feedback(id,run_id,event_id,reaction,reason,created_at) VALUES(?,?,?,?,?,?)",
                (new_id("feedback"), run_id, event_id, reaction, reason, now_iso()),
            )

    def record_provider_usage(self, run_id: str, tokens: int, model: str = "", purpose: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO provider_usage(id,run_id,tokens,model,purpose,created_at) VALUES(?,?,?,?,?,?)",
                (new_id("usage"), run_id, max(0, int(tokens)), model, purpose, now_iso()),
            )

    def provider_usage_totals(self, run_id: str) -> Dict[str, int]:
        month_prefix = now_iso()[:7]
        with self.connect() as conn:
            run_row = conn.execute("SELECT COALESCE(SUM(tokens),0) AS total FROM provider_usage WHERE run_id=?", (run_id,)).fetchone()
            month_row = conn.execute("SELECT COALESCE(SUM(tokens),0) AS total FROM provider_usage WHERE substr(created_at,1,7)=?", (month_prefix,)).fetchone()
        return {"run_tokens": int(run_row["total"]), "month_tokens": int(month_row["total"])}

    def provider_budget_error(self, run_id: str, requested_tokens: int, config: Dict[str, Any]) -> Optional[str]:
        totals = self.provider_usage_totals(run_id)
        run_limit = int(config.get("run_token_budget") or 0)
        month_limit = int(config.get("monthly_token_budget") or 0)
        if run_limit and totals["run_tokens"] + requested_tokens > run_limit:
            return f"单次 Run token 预算已到上限（{run_limit}）"
        if month_limit and totals["month_tokens"] + requested_tokens > month_limit:
            return f"本月 token 预算已到上限（{month_limit}）"
        return None

    def run_dir(self, project_id: str, run_id: str) -> Path:
        path = self.project_dir(project_id) / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path


store = Store()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
