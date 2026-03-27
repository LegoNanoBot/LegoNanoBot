"""Tool registry for dynamic tool management."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.xray.observer import XRayObserver


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.observer: XRayObserver | None = None

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any], run_id: str = "") -> str:
        """Execute a tool by name with given parameters."""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        if self.observer and run_id:
            try:
                await self.observer.emit(
                    run_id=run_id,
                    event_type="tool_call_start",
                    data={"tool_name": name, "arguments": params or {}}
                )
            except Exception:
                pass

        t0 = time.time()
        try:
            # Attempt to cast parameters to match schema types
            params = tool.cast_params(params)
            
            # Validate parameters
            errors = tool.validate_params(params)
            if errors:
                result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
            else:
                result = await tool.execute(**params)
                if isinstance(result, str) and result.startswith("Error"):
                    result = result + _HINT
        except Exception as e:
            result = f"Error executing {name}: {str(e)}" + _HINT

        if self.observer and run_id:
            try:
                duration_s = time.time() - t0
                result_str = result if isinstance(result, str) else str(result)
                await self.observer.emit(
                    run_id=run_id,
                    event_type="tool_call_end",
                    data={"tool_name": name, "result_preview": result_str[:500], "result": result_str[:20000], "duration_s": duration_s}
                )
            except Exception:
                pass

        return result

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
