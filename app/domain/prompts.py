"""可审计提示词的稳定领域契约。"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Iterable


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    version: str
    content: str

    def manifest(self) -> dict[str, str]:
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "content_sha256": _sha256(self.content.encode("utf-8")),
        }


def build_prompt_manifest(prompts: Iterable[PromptSpec]) -> dict[str, object]:
    entries = [item.manifest() for item in prompts]
    return {
        "schema_version": "risk-agent-prompt-manifest/v1",
        "prompts": entries,
        "manifest_sha256": _sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }
