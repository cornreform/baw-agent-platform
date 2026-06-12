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
    """Register a tool with BAW's tool registry.

    Validates the schema (see ``core.tool_schema``) before storing.
    Hard failures raise ``ToolSchemaError`` so the bug surfaces at
    registration time, not silently at first-call time.
    """
    from .tool_schema import validate_tool_def
    tool_def = {
        "name": name,
        "description": description,
        "handler": handler,
        "parameters": parameters,
        "risk_level": risk_level,
    }
    warnings = validate_tool_def(tool_def, source=name)
    if warnings:
        import sys as _sys
        print(
            f"[tool_schema] WARN registering {name!r}:",
            file=_sys.stderr,
        )
        for w in warnings:
            print(f"  - {w}", file=_sys.stderr)
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


def execute_tool(name: str, arguments: dict, timeout: int = 30) -> str:
    """Execute a tool by name with validated arguments + timeout guard (kimi B5)."""
    tool = get_tool(name)
    if not tool:
        from .guards import bail
        return bail("tool_unknown", tool=name)
    import time as _t
    import concurrent.futures

    _start = _t.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.handler, **arguments)
            result = future.result(timeout=timeout)
        _dur = _t.time() - _start
        try:
            from .evolve import track_tool_call
            track_tool_call(name, arguments, success=True, duration=_dur)
        except Exception:
            pass
        return str(result)
    except concurrent.futures.TimeoutError:
        _dur = _t.time() - _start
        try:
            from .evolve import track_tool_call
            track_tool_call(name, arguments, success=False, duration=_dur, error="timeout")
        except Exception:
            pass
        from .guards import bail
        return bail("tool_timeout", tool=name, timeout=timeout)
    except Exception as e:
        _dur = _t.time() - _start
        try:
            from .evolve import track_tool_call
            track_tool_call(name, arguments, success=False, duration=_dur, error=str(e))
        except Exception:
            pass
        return f"Error executing {name}: {e}"


def clear():
    """Clear all registered tools (for testing)."""
    _tools.clear()
