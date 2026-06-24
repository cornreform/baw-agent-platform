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
    """Execute a tool by name with validated arguments + timeout guard + auto-retry."""
    tool = get_tool(name)
    if not tool:
        from .guards import bail
        return bail("tool_unknown", tool=name)
    import time as _t
    import concurrent.futures

    # ── Safety Gate: Pre-execution check ──
    try:
        from .guards import check_safety
        blocked, reason = check_safety(name, arguments)
        if blocked:
            return reason
    except ImportError:
        pass

    MAX_ATTEMPTS = 3
    last_error = ""
    attempt = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        pool = None
        _start = _t.time()
        try:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(tool.handler, **arguments)
            result = future.result(timeout=timeout)
            _dur = _t.time() - _start
            try:
                from .evolve import track_tool_call
                track_tool_call(name, arguments, success=True, duration=_dur)
            except Exception as _e:
                import logging
                logging.getLogger("baw.tools").debug(f"track_tool_call failed: {_e}")
            return str(result)
        except concurrent.futures.TimeoutError:
            _dur = _t.time() - _start
            try:
                from .evolve import track_tool_call
                track_tool_call(name, arguments, success=False, duration=_dur, error="timeout")
            except Exception as _e:
                import logging
                logging.getLogger("baw.tools").debug(f"track_tool_call timeout failed: {_e}")
            last_error = f"timeout after {timeout}s"
            if attempt < MAX_ATTEMPTS:
                # Non-idempotent tools: don't retry on timeout (may have partially executed)
                _NON_IDEMPOTENT = {"write_file", "patch", "delegate_task", "bash"}
                if name in _NON_IDEMPOTENT:
                    break
                timeout = min(timeout * 2, 120)  # Double timeout each retry
        except Exception as e:
            _dur = _t.time() - _start
            error_str = str(e)
            try:
                from .evolve import track_tool_call
                track_tool_call(name, arguments, success=False, duration=_dur, error=error_str)
            except Exception:
                pass
            last_error = error_str

            # ── Auto-heal: missing Python module ──
            # If a tool crashes because of a missing dependency (ModuleNotFoundError),
            # parse the module name, pip install it, and retry automatically.
            # BAW should never fail on a missing pip package — it should heal itself.
            _auto_installed = False
            _mod_name = ""
            if isinstance(e, ModuleNotFoundError):
                _mod_name = str(e).split("'")[1] if "'" in str(e) else ""
            elif "No module named" in error_str:
                import re as _re
                _m = _re.search(r"No module named ['\"]?([^'\"]+)['\"]?", error_str)
                if _m:
                    _mod_name = _m.group(1)
            if _mod_name and _mod_name not in ("pip", "setuptools"):
                try:
                    import subprocess as _sp
                    import sys as _sys
                    import logging as _log
                    _log.getLogger("baw.tools").warning(
                        f"[AutoHeal] Missing module '{_mod_name}' — attempting auto-install..."
                    )
                    _r = _sp.run(
                        [_sys.executable, "-m", "pip", "install", _mod_name, "--quiet"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if _r.returncode == 0:
                        _auto_installed = True
                        _log.getLogger("baw.tools").info(
                            f"[AutoHeal] ✅ Installed '{_mod_name}', retrying tool call..."
                        )
                        # Restart the loop with the same attempt counter
                        # (we'll retry with attempt unchanged since we fixed the issue)
                        last_error = ""
                        continue  # retry this attempt
                    else:
                        _log.getLogger("baw.tools").error(
                            f"[AutoHeal] ❌ Failed to auto-install '{_mod_name}': {_r.stderr[:200]}"
                        )
                except Exception as _pip_e:
                    import logging as _log
                    _log.getLogger("baw.tools").error(
                        f"[AutoHeal] ❌ pip install failed: {_pip_e}"
                    )
            
            # ── Auto-correct common failures ──
            if attempt < MAX_ATTEMPTS:
                # If bash command fails with exit code, don't retry — it's user's command
                if name == "bash" and "exit code" in error_str.lower():
                    break
                # If syntax error or path issue, try different path
                if "invalid syntax" in error_str.lower() or "no such file" in error_str.lower():
                    if name == "bash" and "path" in str(arguments.get("command", "")):
                        # Try alternate path
                        pass  # will retry
        finally:
            if pool is not None:
                pool.shutdown(wait=False)
    
    # All attempts exhausted
    from .guards import bail
    if last_error == f"timeout after {timeout}s":
        return bail("tool_timeout", tool=name, timeout=timeout, attempts=attempt)
    return f"Error executing {name} (tried {attempt}x): {last_error}"
def clear():
    """Clear all registered tools (for testing)."""
    _tools.clear()
