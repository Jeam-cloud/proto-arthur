"""Tool registry — enforces privilege separation.

`for_mode()` is the only way the agent loop obtains tools, so the "current
task mode" decision is made exactly once per message, in one place. Unknown
tool names or out-of-mode requests never reach execution.
"""

from __future__ import annotations

from tools.base import TaskMode, Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"{tool.__class__.__name__} has no name")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def for_mode(self, mode: TaskMode) -> list[Tool]:
        return [t for t in self._tools.values() if mode in t.modes]

    def get_granted(self, name: str, mode: TaskMode) -> Tool | None:
        """None for unknown names AND for tools outside the current mode —
        callers can't accidentally distinguish and leak capability info."""
        tool = self._tools.get(name)
        if tool is None or mode not in tool.modes:
            return None
        return tool

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())
