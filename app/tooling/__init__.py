"""Strongly typed local tool registry; MCP discovery is intentionally disabled in V1."""

from .registry import ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolSpec"]
