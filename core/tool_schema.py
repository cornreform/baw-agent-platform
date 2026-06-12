"""BAW — TOOL_DEF schema validator + canonical shape.

Single source of truth for what a BAW tool's TOOL_DEF MUST look like.

The 2026-06-12 sub-agent was bitten twice by TOOL_DEF drift:
  1. Added ``examples`` key to ``http_fetch.TOOL_DEF`` → ``register()``
     raised ``unexpected keyword argument 'examples'``.
  2. Forgot the ``handler`` key → ``register()`` raised
     ``missing 1 required positional argument: 'handler'``.

Both bugs were "reactive" — discovered one tool at a time, fixed in
isolation, no way for the next sub-agent to know the schema. This
module fixes the class of bug.

**Canonical TOOL_DEF shape**::

    TOOL_DEF = {
        "name": str,           # required, must equal the module name
        "description": str,    # required, one-line summary for the LLM
        "handler": callable,   # required, the entry function
        "parameters": dict,    # required, JSON Schema
        "risk_level": str,     # required, "low" | "medium" | "high"
    }

**Disallowed keys** (raised as warnings during validate)::
    - ``examples`` — not a registered field, removed from the call args
      before passing to ``register()``.

**Validation runs at three points**:
  1. ``register()`` is wrapped to call ``validate_tool_def()`` first.
     Failures raise ``ToolSchemaError``.
  2. ``baw self-test`` runs ``validate_all_tools()`` over every
     registered tool and reports drift.
  3. CI / pre-deploy hook can call ``validate_tool_def_file(path)`` to
     audit a single tool file without importing it.

Risk levels:
  - ``"low"``: read-only, no side effects, no network mutation
    (web_search, web_extract, http_fetch, read_file, search_files,
    memory read, vision).
  - ``"medium"``: writes local files or makes outbound stateful calls
    (write_file, patch, tts, image_generate, http_fetch with PUT,
    petrestaurants refresh, restaurant cache writes).
  - ``"high"``: runs arbitrary code, mutates config, deletes files,
    network requests with destructive side effects (bash, execute_code,
    delegate_task, browser, restaurant with pet-friendliness intersect).
"""
from __future__ import annotations
import ast
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ── Canonical schema ───────────────────────────────────────

REQUIRED_KEYS = ("name", "description", "handler", "parameters", "risk_level")
ALLOWED_KEYS = REQUIRED_KEYS  # no extras for now
RISK_LEVELS = ("low", "medium", "high")


class ToolSchemaError(Exception):
    pass


def validate_tool_def(tool_def: Dict[str, Any], *, source: str = "<unknown>") -> List[str]:
    """Validate a TOOL_DEF dict. Returns a list of warnings.

    Raises ``ToolSchemaError`` on hard failures (missing required keys,
    wrong types, invalid risk_level). Soft warnings (extra keys,
    description too long, etc.) are returned so the caller can log
    them without aborting.
    """
    if not isinstance(tool_def, dict):
        raise ToolSchemaError(
            f"{source}: TOOL_DEF must be a dict, got {type(tool_def).__name__}"
        )

    warnings: List[str] = []

    # Required keys present
    missing = [k for k in REQUIRED_KEYS if k not in tool_def]
    if missing:
        raise ToolSchemaError(
            f"{source}: TOOL_DEF missing required keys: {missing}. "
            f"Required = {list(REQUIRED_KEYS)}."
        )

    # Type checks
    if not isinstance(tool_def["name"], str) or not tool_def["name"].strip():
        raise ToolSchemaError(f"{source}: TOOL_DEF['name'] must be a non-empty string")
    if not isinstance(tool_def["description"], str):
        raise ToolSchemaError(f"{source}: TOOL_DEF['description'] must be a string")
    if not callable(tool_def["handler"]):
        raise ToolSchemaError(
            f"{source}: TOOL_DEF['handler'] must be callable, got "
            f"{type(tool_def['handler']).__name__}"
        )
    if not isinstance(tool_def["parameters"], dict):
        raise ToolSchemaError(f"{source}: TOOL_DEF['parameters'] must be a dict (JSON Schema)")
    if tool_def["parameters"].get("type") != "object":
        warnings.append(
            f"{source}: TOOL_DEF['parameters']['type'] is "
            f"{tool_def['parameters'].get('type')!r}, expected 'object'. "
            f"BAW tool dispatch assumes an object parameter."
        )
    if tool_def["risk_level"] not in RISK_LEVELS:
        raise ToolSchemaError(
            f"{source}: TOOL_DEF['risk_level'] must be one of {RISK_LEVELS}, "
            f"got {tool_def['risk_level']!r}"
        )

    # Soft: extra keys
    extras = [k for k in tool_def if k not in ALLOWED_KEYS]
    if extras:
        warnings.append(
            f"{source}: TOOL_DEF has extra keys {extras}. "
            f"Allowed = {list(ALLOWED_KEYS)}. "
            f"Common mistake: 'examples' is not registered and will be "
            f"rejected by register()."
        )

    # Soft: description length
    desc = tool_def["description"]
    if len(desc) > 600:
        warnings.append(
            f"{source}: TOOL_DEF['description'] is {len(desc)} chars; "
            f"keep under 600 for the LLM context budget."
        )
    if len(desc) < 20:
        warnings.append(
            f"{source}: TOOL_DEF['description'] is {len(desc)} chars; "
            f"add a one-line usage hint so sub-agents know when to call this."
        )

    # Soft: name sanity
    if not re.match(r"^[a-z][a-z0-9_]*$", tool_def["name"]):
        warnings.append(
            f"{source}: TOOL_DEF['name'] {tool_def['name']!r} should be "
            f"lowercase snake_case (matches the module filename)."
        )

    return warnings


def validate_tool_def_file(path: Path) -> Tuple[bool, List[str]]:
    """Static-validate a tool file by parsing its TOOL_DEF AST.

    Useful for CI / pre-commit. Does NOT import the module — just checks
    the dict literal shape so we can audit without side effects.
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return False, [f"{path}: SyntaxError: {e}"]

    found = False
    issues: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "TOOL_DEF":
                    found = True
                    if not isinstance(node.value, ast.Dict):
                        issues.append(f"{path}: TOOL_DEF must be a dict literal")
                        continue
                    keys = []
                    for k in node.value.keys:
                        if isinstance(k, ast.Constant):
                            keys.append(k.value)
                        else:
                            keys.append("<dynamic>")
                    missing = [k for k in REQUIRED_KEYS if k not in keys]
                    if missing:
                        issues.append(
                            f"{path}: TOOL_DEF missing keys: {missing}"
                        )
                    if "risk_level" in keys:
                        # Find the value
                        for k, v in zip(node.value.keys, node.value.values):
                            if (isinstance(k, ast.Constant)
                                    and k.value == "risk_level"
                                    and isinstance(v, ast.Constant)
                                    and v.value not in RISK_LEVELS):
                                issues.append(
                                    f"{path}: risk_level {v.value!r} not in {RISK_LEVELS}"
                                )
                    extras = [k for k in keys if k not in ALLOWED_KEYS and k != "<dynamic>"]
                    if extras:
                        issues.append(
                            f"{path}: TOOL_DEF has extra keys: {extras}. "
                            f"Allowed = {list(ALLOWED_KEYS)}."
                        )
    if not found:
        issues.append(f"{path}: no TOOL_DEF = ... assignment found")
    return (len(issues) == 0), issues


def validate_all_tools() -> Dict[str, Any]:
    """Validate every registered tool. Run by `baw self-test`.

    Returns a dict with ``ok`` bool, ``per_tool`` map, and ``summary``.
    """
    from core.tools import list_tools
    per_tool: Dict[str, List[str]] = {}
    ok = True
    total = 0
    hard_fail = 0
    warn_count = 0
    for tool_def in list_tools():
        total += 1
        name = tool_def.name
        try:
            warnings = validate_tool_def(
                {
                    "name": tool_def.name,
                    "description": tool_def.description,
                    "handler": tool_def.handler,
                    "parameters": tool_def.parameters,
                    "risk_level": tool_def.risk_level,
                },
                source=name,
            )
            if warnings:
                warn_count += 1
            per_tool[name] = warnings
        except ToolSchemaError as e:
            per_tool[name] = [f"HARD FAIL: {e}"]
            ok = False
            hard_fail += 1
    return {
        "ok": ok,
        "total": total,
        "hard_fail": hard_fail,
        "warn_count": warn_count,
        "per_tool": per_tool,
        "summary": (
            f"{total} tools validated, {warn_count} with warnings, "
            f"{hard_fail} hard-fail"
        ),
    }
