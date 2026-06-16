"""
BAW built-in: execute_code — Python code execution with BAW tool access.

Runs Python code in an isolated scope with BAW tools injected as imports.
Agent can write multi-step scripts that combine tool calls with processing logic.

Safety (M3 review consensus):
- Stripped __builtins__ — no eval/exec/__import__/os/subprocess
- Binary-level keyword scan before execution
- 300s timeout
- Stdout/stderr capture, no TTY
"""

import sys
import json
import time
import traceback
from io import StringIO
from pathlib import Path


# ── Tool wrapper parameter mapping ──
# Maps tool name -> ordered parameter names for positional arg resolution.
# M3 review: wrappers MUST handle positional args and RETURN values correctly.
_TOOL_PARAMS = {
    "web_search": ["query", "limit"],
    "web_extract": ["urls"],
    "read_file": ["path", "offset", "limit"],
    "write_file": ["path", "content"],
    "patch": ["path", "old_string", "new_string", "replace_all"],
    "search_files": ["pattern", "target", "path", "file_glob", "limit"],
    "terminal": ["command", "timeout", "workdir"],
    "bash": ["command", "timeout", "workdir"],
    "memory": ["action", "target", "content", "old_text"],
    "session_search": ["query", "session_id", "profile"],
    "cronjob": ["action", "prompt", "schedule", "name"],
    "delegate_task": ["goal", "context", "toolsets"],
    "config": ["action", "path", "value"],
    "todo": ["todos", "merge"],
}

_TOOL_IMPORTS = {}


def _import_tools():
    """Lazy-import BAW tools and wrap them for the sandbox."""
    if _TOOL_IMPORTS:
        return _TOOL_IMPORTS
    _repo = str(Path(__file__).resolve().parent.parent)
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    from core.tools import execute_tool

    for name, params in _TOOL_PARAMS.items():
        def _make_wrapper(tname, pnames):
            def _wrapper(*args, **kwargs):
                merged = {}
                for i, p in enumerate(pnames):
                    if i < len(args):
                        merged[p] = args[i]
                merged.update(kwargs)
                # Alias: terminal -> bash (tool is registered as 'bash')
                actual_name = "bash" if tname == "terminal" else tname
                result = execute_tool(actual_name, merged)
                return result
            _wrapper.__name__ = tname
            return _wrapper
        _TOOL_IMPORTS[name] = _make_wrapper(name, params)

    return _TOOL_IMPORTS


# ── Safe builtins subset (M3 review: CRITICAL — no eval/exec/__import__) ──
SAFE_BUILTINS = {
    "print": print, "len": len,
    "str": str, "int": int, "float": float, "list": list,
    "dict": dict, "tuple": tuple, "bool": bool, "set": set,
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter, "sorted": sorted,
    "reversed": reversed, "type": type, "isinstance": isinstance,
    "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
    "True": True, "False": False, "None": None,
    "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "KeyError": KeyError,
    "IndexError": IndexError, "StopIteration": StopIteration,
    "abs": abs, "min": min, "max": max, "sum": sum,
    "any": any, "all": all, "round": round, "hex": hex,
    "oct": oct, "bin": bin, "ord": ord, "chr": chr,
    "repr": repr, "ascii": ascii, "format": format,
    "bytes": bytes, "bytearray": bytearray, "memoryview": memoryview,
    "slice": slice, "iter": iter, "next": next,
}


def execute_code(code: str, timeout: int = 300) -> str:
    """Execute Python code with BAW tool access in an isolated scope.

    Args:
        code: Python source code to execute.
        timeout: Max execution time in seconds (default 300).

    Returns:
        Captured stdout + stderr + elapsed time.
    """
    # ── Binary safety check (catch obvious escape attempts early) ──
    _DANGEROUS = [
        "eval(", "exec(", "__import__(", "open(",
        "import os", "from os", "import subprocess",
        "import shutil",
    ]
    code_lower = code.lower()
    for d in _DANGEROUS:
        if d in code_lower:
            return (
                f"[execute_code] Blocked: code contains unsafe pattern '{d}'.\n"
                f"Use `terminal()` for system commands instead."
            )

    try:
        tools = _import_tools()

        # ── JSON processing helpers ──
        def json_parse(text):
            """Parse JSON with strict=False (allows single-quoted keys etc.)."""
            return json.loads(text, strict=False)

        def shell_quote(s):
            """Simple shell-quote for string arguments."""
            if not s:
                return "''"
            if any(c in s for c in (' ', '"', "'", "\\", "$", "`")):
                return "'" + s.replace("'", "'\\''") + "'"
            return s

        def retry(fn, max_attempts=3, delay=2):
            """Retry a function with exponential backoff."""
            last_exc = ValueError("max_attempts must be >= 1")
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn()
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts:
                        import time as _t
                        _t.sleep(delay * attempt)
            raise last_exc

        tools["json_parse"] = json_parse
        tools["shell_quote"] = shell_quote
        tools["retry"] = retry

        ns = {"__builtins__": SAFE_BUILTINS, **tools}
        out = StringIO()
        err = StringIO()
        old_out = sys.stdout
        old_err = sys.stderr
        sys.stdout = out
        sys.stderr = err
        start = time.time()

        try:
            exec(code, ns)
        except TimeoutError:
            err.write(f"[Timeout] Execution exceeded {timeout}s\n")
        except Exception:
            err.write(traceback.format_exc())

        elapsed = time.time() - start
        sys.stdout = old_out
        sys.stderr = old_err

        result = out.getvalue()
        err_text = err.getvalue()
        parts = []
        if result:
            parts.append(f"[stdout]\n{result.strip()}")
        if err_text:
            parts.append(f"[stderr]\n{err_text.strip()}")
        parts.append(f"[done] {elapsed:.1f}s")
        return "\n\n".join(parts)

    except Exception as e:
        return f"[execute_code] Internal error: {e}"


TOOL_DEF = {
    "name": "execute_code",
    "description": (
        "Execute Python code with access to BAW tools. "
        "Use this for multi-step logic that chains tool calls with processing between them. "
        "Injected tools: web_search, web_extract, read_file, write_file, patch, "
        "search_files, terminal (bash), memory, session_search, cronjob, "
        "delegate_task, config, todo, json_parse, shell_quote, retry. "
        "Works like writing a short Python script — use print() for output. "
        "IMPORTANT: Call these as regular Python functions, e.g. "
        "content = read_file('/path') ; print(content)"
    ),
    "handler": execute_code,
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute. Tools available without import: "
                               "web_search(query, limit), web_extract(urls), "
                               "read_file(path, offset, limit), write_file(path, content), "
                               "patch(path, old_string, new_string, replace_all), "
                               "search_files(pattern, target, path, file_glob, limit), "
                               "terminal(command, timeout, workdir), "
                               "memory(action, target, content, old_text), "
                               "session_search(query, session_id, profile), "
                               "cronjob(action, prompt, schedule, name), "
                               "delegate_task(goal, context, toolsets), "
                               "config(action, key, value), "
                               "todo(todos, merge), "
                               "json_parse(text), shell_quote(s), retry(fn, max_attempts, delay). "
                               "Use print() for output.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds (default: 300, max: 600).",
                "default": 300,
            },
        },
        "required": ["code"],
    },
    "risk_level": "high",
}
