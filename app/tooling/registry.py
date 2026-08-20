from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    stage: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], Any]
    deterministic: bool = True
    local_only: bool = True

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "deterministic": self.deterministic,
            "local_only": self.local_only,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        stage: str,
        description: str,
        input_model: type[T],
        handler: Callable[[T], Any],
    ) -> None:
        if name in self._tools:
            raise ValueError(f"DUPLICATE_TOOL: {name}")
        self._tools[name] = ToolSpec(name, stage, description, input_model, handler)  # type: ignore[arg-type]

    def invoke(self, name: str, payload: dict[str, Any]) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"UNKNOWN_TOOL: {name}") from exc
        arguments = tool.input_model.model_validate(payload)
        return tool.handler(arguments)

    def manifest(self) -> dict[str, Any]:
        return {
            "protocol": "risk-local-tool-registry/v1",
            "mcp": {"enabled": False, "adapter_boundary": "/api/v1/tools/mcp-adapter"},
            "tools": [self._tools[name].manifest() for name in sorted(self._tools)],
        }
