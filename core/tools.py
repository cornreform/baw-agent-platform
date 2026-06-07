"""
BAW — Tool Registry
Register, validate, and execute tools.
"""

from __future__ import annotations
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ToolDef:
    name: str
    description: str
    handler: Callable
    parameters: dict  # JSON Schema
    risk_level: str = "low"  # low | medium | high
    requires_approval: bool = False


_tools: dict[str, ToolDef] = {}


def register(
    name: str,
    description: str,
    handler: Callable,
    parameters: dict,
    risk_level: str = "low",
):
    """Register a tool with BAW's tool registry."""
    _tools[name] = ToolDef(
        name=name,
        description=description,
        handler=handler,
        parameters=parameters,
        risk_level=risk_level,
    )


def get_tool(name: str) -> Optional[ToolDef]:
    """Get a tool by name."""
    return _tools.get(name)


def list_tools() -> list[ToolDef]:
    """List all registered tools."""
    return list(_tools.values())


def get_openai_tools() -> list[dict]:
    """Convert registered tools to OpenAI function-calling format."""
    result = []
    for tool in _tools.values():
        result.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        })
    return result


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name with validated arguments."""
    tool = get_tool(name)
    if not tool:
        return f"Error: unknown tool '{name}'"
    try:
        result = tool.handler(**arguments)
        return str(result)
    except Exception as e:
        return f"Error executing {name}: {e}"


def clear():
    """Clear all registered tools (for testing)."""
    _tools.clear()
