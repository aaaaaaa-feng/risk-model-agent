"""确定性 Pipeline 对 Tool Registry 暴露的稳定契约。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunToolInput(BaseModel):
    run_id: str = Field(min_length=4)
    state: dict[str, Any]
