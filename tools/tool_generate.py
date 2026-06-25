from __future__ import annotations
"""BAW built-in: tool_generate — create new tools autonomously.

Takes a prompt describing what tool to build, generates Python code
via LLM, validates it, registers it, and runs smoke test.
Implements BAW's self-extension capability.
"""
import json
import os
import sys
import subprocess
import shutil
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_TOOLS_DIR = _BAW_HOME / "tools"


def _syntax_check(path: Path) -> dict:
    """Check Python syntax of a file."""
    try:
        import ast
        with open(path) as f:
            ast.parse(f.read())
        return {"ok": True}
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_GENERATE_PROMPT_TEMPLATE = """You are a Python code generator for BAW agent tools.
Generate a complete, working Python tool file at: {tools_dir}/{name}.py

The tool must follow this exact pattern (a working example from the codebase):

```python
\"\"\"BAW built-in: {name} — {description}
\"\"\"
import json
from pathlib import Path

def _handler(params...) -> str:
    \"\"\"description\"\"\"
    # Implementation here
    result = {{"ok": True, ...}}
    return json.dumps(result, ensure_ascii=False, indent=2)

TOOL_DEF = {{
    "name": "{name}",
    "description": (...),
    "handler": _handler,
    "parameters": {{
        "type": "object",
        "properties": {{...}},
        "required": [],
    }},
    "risk_level": "low",
}}
```

Requirements:
- The tool MUST have a `_handler` function that returns a JSON string
- The tool MUST have a `TOOL_DEF` dict at module level with name, description, handler, parameters, risk_level
- name="{name}", description="{description}"
- What it does: {what_it_does}
- Use json.dumps(..., ensure_ascii=False, indent=2) for output

=== YAGNI DECISION LADDER (follow this before writing any code) ===
1. Does this code need to exist?          → NO: skip it (YAGNI)
2. Can stdlib / built-ins handle it?      → YES: use them, NO extra deps
3. Is there a native platform feature?     → YES: use it
4. Is there already an installed dep?      → YES: use it, don't add another
5. Can it be a one-liner?                 → YES: one line
6. Only then: write the minimum that works

=== NEVER skip (even for YAGNI) ===
- Input validation / trust-boundary checks
- Data-loss prevention
- Security (auth, injection)
- Clear error messages
- Proper docstrings and type hints

If you skip something (stdlib handles it, no need for this feature yet),
leave a # [YAGNI] comment explaining why.

- Risk level: "low" for read-only, "medium" for writes, "high" for destructive ops
- Keep it simple — use Path from pathlib for file operations
- Do NOT include any test or main block

Generate ONLY valid Python code. No markdown fences, no explanations.
Start with \"\"\"BAW built-in: {name}\"\"\"
"""


def _build_generate_prompt(name: str, description: str, what_it_does: str) -> str:
    """Build the LLM prompt for generating tool code."""
    return _GENERATE_PROMPT_TEMPLATE.format(
        tools_dir=str(_BAW_HOME / "tools"),
        name=name,
        description=description,
        what_it_does=what_it_does,
    )


def _call_llm_for_code(prompt: str) -> str:
    """Call LLM to generate tool code from a prompt."""
    from core.llm import call_llm_with_fallback

    sys_prompt = (
        "You are a code generator for BAW agent tools. "
        "Generate clean, working Python. No markdown formatting, no explanations — just the code."
    )

    result = call_llm_with_fallback(
        {},
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000,
    )
    code = result.response.content.strip() if result.response else ""
    # Strip markdown fences if present
    if code.startswith("```"):
        code = code.split("\n", 1)[1] if "\n" in code else code
        code = code.rsplit("```", 1)[0] if "```" in code else code
    # Remove trailing/leading whitespace
    code = code.strip()
    return code


def _generate_code(name: str, description: str, what_it_does: str) -> str:
    """Use LLM to generate tool code."""
    try:
        sys.path.insert(0, str(_BAW_HOME))
        prompt = _build_generate_prompt(name, description, what_it_does)
        return _call_llm_for_code(prompt)
    except Exception as e:
        return f"# ERROR generating code: {e}\n"


def _write_code(name: str, code: str) -> dict:
    """Write generated code to tools/<name>.py."""
    path = _TOOLS_DIR / f"{name}.py"
    try:
        path.write_text(code + "\n")
        if not path.exists():
            return {"ok": False, "error": "File was not written"}
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _update_existing_import_line(content: str, name: str) -> str:
    """Update an existing 'from . import (...' line to include the new tool name."""
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if "from . import (" in line and "bash" in line and name not in line:
            if line.strip().endswith("("):
                new_lines.append(line)
                new_lines.append(f"               {name},")
            elif line.strip().endswith(")"):
                new_lines.append(line[:-1] + f", {name})")
            else:
                new_lines.append(line.rstrip() + f",\n               {name}")
        else:
            new_lines.append(line)
    return "\n".join(new_lines)


def _add_new_import_section(content: str, name: str) -> str:
    """Add a new import line and registration for a tool not in the existing import block."""
    content = content.replace(
        "def register_all():",
        f"from . import {name}\n\n\ndef register_all():",
    )
    lines = content.split("\n")
    last_reg = -1
    for i, line in enumerate(lines):
        if "register(**" in line:
            last_reg = i
    if last_reg >= 0:
        lines.insert(last_reg + 1, f"    register(**{name}.TOOL_DEF)")
        content = "\n".join(lines)
    return content


def _update_init(name: str) -> dict:
    """Add tool import and registration to tools/__init__.py."""
    init_path = _TOOLS_DIR / "__init__.py"
    if not init_path.exists():
        return {"ok": False, "error": "__init__.py not found"}

    try:
        content = init_path.read_text()
    except Exception as e:
        return {"ok": False, "error": f"Failed to read __init__.py: {e}"}

    # Check if already imported
    if f"from . import ({name}" in content or f", {name})" in content:
        return {"ok": False, "error": f"Tool '{name}' already imported"}

    # Add import (modify the import line)
    import_line = "from . import (bash, read_file, write_file, web_search, image_generate, tts, todo,"
    if import_line in content:
        content = _update_existing_import_line(content, name)
    else:
        content = _add_new_import_section(content, name)

    try:
        init_path.write_text(content)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"Failed to write __init__.py: {e}"}


def _smoke_test(name: str) -> dict:
    """Run a simple smoke test: import the tool."""
    try:
        # Test syntax
        syntax = _syntax_check(_TOOLS_DIR / f"{name}.py")
        if not syntax["ok"]:
            return {"ok": False, "error": syntax["error"]}

        # Test import
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open('{_TOOLS_DIR / name}.py').read()); print('syntax OK')"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[:300]}

        return {"ok": True, "message": "Syntax check passed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get_or_generate_code(name: str, description: str, what_it_does: str,
                          skip_generate: bool, code: str) -> tuple[str | None, str | None]:
    """Get provided code or generate via LLM. Returns (code, error_json_or_None)."""
    if skip_generate and code:
        return code, None
    if what_it_does:
        tool_code = _generate_code(name, description, what_it_does)
        if tool_code.startswith("# ERROR"):
            return None, json.dumps({"ok": False, "error": tool_code}, ensure_ascii=False)
        return tool_code, None
    return None, json.dumps(
        {"ok": False, "error": "Either what_it_does or code is required"}, ensure_ascii=False)


def _build_tool_result(ok: bool, name: str, tool_code: str,
                       error: str | None = None) -> str:
    """Build the final JSON result for tool creation."""
    if ok:
        return json.dumps({
            "ok": True,
            "message": f"Tool '{name}' created successfully",
            "path": str(_TOOLS_DIR / f"{name}.py"),
            "generated_code": tool_code[:200],
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "ok": False,
        "error": error,
        "generated_code": tool_code[:500],
    }, ensure_ascii=False)


def _execute_create_pipeline(name: str, tool_code: str) -> str:
    """Run write → syntax check → init registration → smoke test pipeline."""
    write_result = _write_code(name, tool_code)
    if not write_result["ok"]:
        return json.dumps(write_result, ensure_ascii=False)

    syntax = _syntax_check(Path(write_result["path"]))
    if not syntax["ok"]:
        Path(write_result["path"]).unlink(missing_ok=True)
        return _build_tool_result(False, name, tool_code,
                                  f"Syntax error: {syntax['error']}")

    init_result = _update_init(name)
    if not init_result["ok"]:
        return _build_tool_result(False, name, tool_code,
                                  init_result["error"])

    test = _smoke_test(name)
    if not test["ok"]:
        return _build_tool_result(False, name, tool_code,
                                  f"Smoke test failed: {test['error']}")

    return _build_tool_result(True, name, tool_code)


def _handler(
    name: str = "",
    description: str = "",
    what_it_does: str = "",
    skip_generate: bool = False,
    code: str = "",
) -> str:
    """Generate a new BAW tool from a prompt.

    Args:
        name: Tool name (lowercase, no spaces, e.g. 'weather')
        description: Brief description (one sentence)
        what_it_does: Detailed description of what the tool should do
        skip_generate: If True, use `code` param directly instead of LLM generation
        code: Python code to use (only when skip_generate=True)
    """
    if not name:
        return json.dumps({"ok": False, "error": "Tool name is required"}, ensure_ascii=False)

    tool_code, err = _get_or_generate_code(name, description, what_it_does, skip_generate, code)
    if err:
        return err
    assert isinstance(tool_code, str)

    return _execute_create_pipeline(name, tool_code)


TOOL_DEF = {
    "name": "tool_generate",
    "description": (
        "[SELF-EXTENSION] Create a new BAW tool from a description. "
        "Uses LLM to generate Python code, writes it to tools/<name>.py, "
        "registers in __init__.py, runs syntax check and smoke test. "
        "BAW can extend itself with new capabilities autonomously."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name (lowercase, no spaces, e.g. 'weather').",
            },
            "description": {
                "type": "string",
                "description": "Brief one-sentence description of the tool.",
                "default": "",
            },
            "what_it_does": {
                "type": "string",
                "description": "Detailed description of what the tool should do and how.",
                "default": "",
            },
            "skip_generate": {
                "type": "boolean",
                "description": "Skip LLM generation and use `code` parameter directly.",
                "default": False,
            },
            "code": {
                "type": "string",
                "description": "Python code to use as the tool (only used when skip_generate=True).",
                "default": "",
            },
        },
        "required": ["name"],
    },
    "risk_level": "high",
}
