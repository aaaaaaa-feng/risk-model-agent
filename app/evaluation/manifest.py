from __future__ import annotations

import hmac
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from app.agents.prompts import prompt_manifest
from app.core.config import Settings
from app.core.security import sha256_bytes, sha256_file
from app.tooling.registry import ToolRegistry


MANIFEST_SCHEMA = "risk-agent-eval-manifest/v1"
AGENT_GRAPH_VERSION = "risk-model-agent-graph/v1"
REVIEWER_RUBRIC_VERSION = "risk-model-reviewer/v1"
SAFE_EVIDENCE_POLICY_VERSION = "risk-safe-evidence/v2"
ERROR_TAXONOMY_VERSION = "risk-agent-error-taxonomy/v1"

KEY_DEPENDENCIES = (
    "fastapi",
    "langgraph",
    "pandas",
    "numpy",
    "scikit-learn",
    "xgboost",
    "lightgbm",
    "catboost",
    "skops",
)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def build_run_manifest(
    *,
    run_id: str,
    target_task: dict[str, Any],
    dataset: dict[str, Any],
    registry: ToolRegistry,
    settings: Settings,
    started_at: str,
    evaluation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompts = prompt_manifest()
    tool_manifest = registry.manifest()
    lineage = dataset.get("lineage") or {}
    dataset_hash = str(lineage.get("output_sha256") or "")
    if not dataset_hash:
        stored_path = Path(str(dataset.get("stored_path") or ""))
        if stored_path.is_file():
            dataset_hash = sha256_file(stored_path)
    policy = {
        "safe_evidence": SAFE_EVIDENCE_POLICY_VERSION,
        "reviewer_rubric": REVIEWER_RUBRIC_VERSION,
        "error_taxonomy": ERROR_TAXONOMY_VERSION,
        "raw_data_provider_egress": False,
        "hidden_chain_of_thought_recorded": False,
    }
    context = dict(evaluation_context or {})
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": run_id,
        "git_sha": discover_git_sha(),
        "source_tree_sha256": source_tree_hash(),
        "runtime_binary_sha256": runtime_binary_hash(),
        "agent_graph_version": AGENT_GRAPH_VERSION,
        "prompt_manifest": prompts,
        "reviewer_rubric_version": REVIEWER_RUBRIC_VERSION,
        "tool_manifest_hash": canonical_hash(tool_manifest),
        "tools": [
            {
                "name": item["name"],
                "version": item["version"],
                "input_schema_hash": item["input_schema_hash"],
            }
            for item in tool_manifest["tools"]
        ],
        "policy_hash": canonical_hash(policy),
        "policy": policy,
        "provider": {
            "name": settings.provider,
            "api_format": settings.api_format,
            "endpoint_sha256": (
                sha256_bytes(settings.base_url.rstrip("/").encode("utf-8"))
                if settings.base_url
                else ""
            ),
            "model": settings.model,
            "reviewer_model": settings.reviewer_model or settings.model,
            "llm_enabled": bool(settings.llm_enabled),
            "parameters": {
                "main": _model_parameters(settings.provider, settings.model),
                "reviewer": _model_parameters(
                    settings.provider, settings.reviewer_model or settings.model
                ),
            },
        },
        "dataset": {
            "id": dataset["id"],
            "content_sha256": dataset_hash,
            "rows": int(dataset.get("rows") or 0),
            "columns": int(dataset.get("columns") or 0),
        },
        "target_task": {
            "id": target_task["id"],
            "target_name_sha256": sha256_bytes(
                str(target_task.get("target_column") or "").encode("utf-8")
            ),
            "labels_hash": canonical_hash(target_task.get("labels") or {}),
        },
        "environment": environment_manifest(),
        "eval_suite": {
            "case_id": context.get("case_id"),
            "trial_id": context.get("trial_id"),
            "suite_version": context.get("suite_version"),
            "evaluator_version": context.get("evaluator_version"),
            "case_config_sha256": context.get("case_config_sha256"),
        },
        "started_at": started_at,
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    return manifest


def environment_manifest() -> dict[str, Any]:
    dependencies: dict[str, str] = {}
    for name in KEY_DEPENDENCIES:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "not-installed"
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "dependencies": dependencies,
    }


def discover_git_sha() -> str:
    override = os.getenv("RISK_AGENT_GIT_SHA", "").strip()
    if override:
        return override
    root = Path(__file__).resolve().parents[2]
    git_entry = root / ".git"
    git_dir = git_entry
    if git_entry.is_file():
        try:
            value = git_entry.read_text(encoding="utf-8").strip()
        except OSError:
            return "unavailable"
        if not value.startswith("gitdir:"):
            return "unavailable"
        git_dir = (root / value.split(":", 1)[1].strip()).resolve()
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "unavailable"
    if not head.startswith("ref:"):
        return head if len(head) == 40 else "unavailable"
    ref = head.split(":", 1)[1].strip()
    try:
        value = (git_dir / ref).read_text(encoding="utf-8").strip()
        if len(value) == 40:
            return value
    except OSError:
        pass
    try:
        lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unavailable"
    for line in lines:
        if line.startswith(("#", "^")):
            continue
        value, _, name = line.partition(" ")
        if name == ref and len(value) == 40:
            return value
    return "unavailable"


def source_tree_hash() -> str:
    root = Path(__file__).resolve().parents[2]
    candidates = [root / "pyproject.toml"]
    for relative_root, pattern in (("app", "*.py"), ("frontend/src", "*.ts*")):
        directory = root / relative_root
        if directory.is_dir():
            candidates.extend(directory.rglob(pattern))
    entries: list[tuple[str, str]] = []
    for path in sorted({item.resolve() for item in candidates if item.is_file()}):
        try:
            relative = path.relative_to(root.resolve()).as_posix()
            entries.append((relative, sha256_file(path)))
        except (OSError, ValueError):
            continue
    return canonical_hash(entries) if entries else "unavailable"


def runtime_binary_hash() -> str:
    if not getattr(sys, "frozen", False):
        return "not-frozen"
    executable = Path(sys.executable)
    try:
        return sha256_file(executable) if executable.is_file() else "unavailable"
    except OSError:
        return "unavailable"


COMPARABILITY_FIELDS = (
    "git_sha",
    "source_tree_sha256",
    "runtime_binary_sha256",
    "agent_graph_version",
    "tool_manifest_hash",
    "policy_hash",
    "prompt_manifest.manifest_sha256",
    "dataset.content_sha256",
    "provider.name",
    "provider.api_format",
    "provider.endpoint_sha256",
    "provider.llm_enabled",
    "provider.model",
    "provider.reviewer_model",
    "provider.parameters",
    "environment.python",
    "environment.os",
    "eval_suite.suite_version",
    "eval_suite.evaluator_version",
    "eval_suite.case_config_sha256",
)


def compare_manifests(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    for field in COMPARABILITY_FIELDS:
        left = _nested(baseline, field)
        right = _nested(candidate, field)
        if left != right:
            differences.append({"field": field, "baseline": left, "candidate": right})
    return {
        "schema_version": "risk-agent-manifest-comparison/v1",
        "comparable": not differences,
        "differences": differences,
        "warning": (
            None if not differences else "核心运行条件不同；结果差异不能全部归因于模型或 Prompt。"
        ),
    }


def verify_manifest(payload: dict[str, Any], expected_hash: str) -> dict[str, Any]:
    value = dict(payload)
    declared = str(value.pop("manifest_sha256", ""))
    computed = canonical_hash(value)
    if (
        not declared
        or not hmac.compare_digest(declared, expected_hash)
        or not hmac.compare_digest(declared, computed)
    ):
        raise ValueError("RUN_MANIFEST_INTEGRITY_FAILED")
    return payload


def _nested(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _model_parameters(provider: str, model: str) -> dict[str, Any]:
    temperature: int | None = 0
    if provider == "openai" or model.startswith(("gpt-5", "o3", "o4")):
        temperature = None
    return {"temperature": temperature, "max_output_tokens": 2048}
