"""Provider 自检所需的最小提示词，不依赖 Agent 层。"""

from __future__ import annotations

from app.domain.prompts import PromptSpec


CONNECTIVITY_PROMPT = PromptSpec(
    "provider-connectivity",
    "1.0.0",
    'Return exactly a JSON object: {"status":"ok"}.',
)

PROMPTS = (CONNECTIVITY_PROMPT,)
