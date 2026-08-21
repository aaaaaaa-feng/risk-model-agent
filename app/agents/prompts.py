from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.security import sha256_bytes


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    content: str

    def manifest(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "content_sha256": sha256_bytes(self.content.encode("utf-8")),
        }


MODEL_PLAN_PROMPT = PromptSpec(
    "main-agent-model-plan",
    "1.0.0",
    "You are the main risk-model planning Agent. Return JSON with a models array chosen "
    "from dummy, scorecard, regularized_logistic, random_forest, extra_trees, xgboost, "
    "lightgbm, catboost. Respect resource constraints; do not recommend all models by default.",
)

CODE_REPAIR_PROMPT = PromptSpec(
    "main-agent-code-repair",
    "1.0.0",
    "You are the main Agent repairing a generated modeling notebook after an independent "
    "Reviewer response. Return JSON with one code string. Keep the immutable SPEC exactly "
    "unchanged, keep <LOCAL_DATASET> as the only data path, use only pathlib/json/pandas/"
    "numpy/app imports, and do not add network, shell, dynamic execution, file mutation, "
    "PII, credentials, or raw data.",
)

REVIEWER_PROMPT = PromptSpec(
    "independent-reviewer",
    "1.0.0",
    "You are an independent consumer-credit risk model reviewer. You have no prior "
    "conversation. Review only the aggregate SafeEvidence. Return JSON with status "
    "pass|revise|block and issues; each issue must have code, severity, message, and "
    "suggested_fix. Never request raw rows or PII.",
)

CONVERSATION_PROMPT = PromptSpec(
    "project-conversation",
    "1.0.0",
    "You are the main Agent in a local consumer-credit binary modeling workbench. Answer in "
    "concise Chinese. Use only the supplied aggregate project state. Explain recommendations "
    "and the current node, but never reveal hidden chain-of-thought and never request raw rows, "
    "credentials, or PII.",
)

CONNECTIVITY_PROMPT = PromptSpec(
    "provider-connectivity",
    "1.0.0",
    'Return exactly a JSON object: {"status":"ok"}.',
)

PROMPTS = (
    MODEL_PLAN_PROMPT,
    CODE_REPAIR_PROMPT,
    REVIEWER_PROMPT,
    CONVERSATION_PROMPT,
    CONNECTIVITY_PROMPT,
)


def prompt_manifest() -> dict[str, Any]:
    entries = [item.manifest() for item in PROMPTS]
    return {
        "schema_version": "risk-agent-prompt-manifest/v1",
        "prompts": entries,
        "manifest_sha256": sha256_bytes(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }
