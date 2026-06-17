"""BAW built-in: skill_import — convert Hermes/foreign skills to BAW tools.

Reads skills from other systems (Hermes skill.md format, YAML-based tool defs,
etc.), parses them, and converts to BAW-compatible Python tools.

Hermes skill format:
  ---
  name: skill-name
  description: ...
  ---
  markdown body with instructions, parameters, examples

Output: BAW tool in tools/<name>.py with TOOL_DEF + handler
"""
import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger("baw.skill_import")

_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_TOOLS_DIR = _BAW_HOME / "tools"


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a Python module name and file path."""
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Name contains path separators: {name!r}")
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_")
    if not safe:
        raise ValueError(f"Name sanitized to empty: {name!r}")
    return safe.replace("-", "_")


def _parse_hermes_skill(content: str) -> dict:
    """Parse a Hermes skill.md file into structured data."""
    result = {
        "name": "",
        "description": "",
        "version": "",
        "author": "",
        "tags": [],
        "body": "",
        "parameters": {},
    }

    # Strip BOM and leading whitespace
    content = content.lstrip("\ufeff").lstrip()

    # Extract YAML frontmatter (relaxed: allows leading whitespace)
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        result["body"] = fm_match.group(2).strip()

        for line in frontmatter.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")

                if key == "name":
                    result["name"] = val
                elif key == "description":
                    result["description"] = val
                elif key == "version":
                    result["version"] = val
                elif key == "author":
                    result["author"] = val
                elif key == "tags":
                    # Handle: tags: [foo, bar]  or  tags: [\"foo\", \"bar\"]
                    # Strip outer brackets, split by comma, clean each
                    raw = val.strip("[]")
                    items = [t.strip().strip('"').strip("'") for t in raw.split(",")]
                    result["tags"] = [t for t in items if t]
                elif key in ("parameters", "params"):
                    result["parameters"]["raw"] = val
    else:
        result["body"] = content.strip()

    if not result["name"]:
        h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1:
            result["name"] = h1.group(1).strip().lower().replace(" ", "-")

    return result


def _convert_to_tool_code(skill: dict) -> str:
    """Convert parsed skill data into BAW tool Python code.

    Uses repr() for all user-controlled content to prevent injection
    through triple-quotes or other special characters.
    """
    name = skill.get("name", "").replace("-", "_").replace(" ", "_") or "imported_skill"
    description = skill.get("description", f"Imported from skill: {name}")
    body = skill.get("body", "")
    tags = skill.get("tags", [])
    source_name = skill.get("name", "external source")

    # Extract example commands/usage from body
    examples = re.findall(r"```(?:bash|shell)?\n(.*?)```", body, re.DOTALL)

    # Use repr() to safely embed user content into Python source
    desc_escaped = repr(description)
    tags_escaped = json.dumps(tags)
    examples_escaped = json.dumps(examples[:3])
    body_preview = repr(body[:200])
    body_help = repr(body[:500])
    name_escaped = repr(name)

    # Use % formatting instead of f-string to avoid escaping nightmares
    code = (
        '# -*- coding: utf-8 -*-\n'
        f'"""BAW built-in: {name} — {description[:80]}\n'
        '\n'
        'Imported from external skill.\n'
        '"""\n'
        'import json\n'
        'import os\n'
        'import subprocess\n'
        'from pathlib import Path\n'
        '\n'
        '\n'
        '_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))\n'
        '\n'
        '\n'
        'def _handler(\n'
        '    action: str = "run",\n'
        ') -> str:\n'
        '    """Execute this skill/task.\n'
        '\n'
        f'    Imported from: {source_name}\n'
        f'    Original description: {description}\n'
        '\n'
        '    Args:\n'
        '        action: What to do ("run", "describe", "help")\n'
        '    """\n'
        '    if action == "describe":\n'
        '        return json.dumps({\n'
        f'            "name": {name_escaped},\n'
        f'            "description": {desc_escaped},\n'
        f'            "tags": {tags_escaped},\n'
        f'            "body_preview": {body_preview},\n'
        '        }, ensure_ascii=False, indent=2)\n'
        '\n'
        '    if action == "help":\n'
        '        return json.dumps({\n'
        f'            "instructions": {body_help},\n'
        f'            "examples": {examples_escaped},\n'
        '        }, ensure_ascii=False, indent=2)\n'
        '\n'
        '    if action == "run":\n'
        '        return json.dumps({\n'
        '            "ok": True,\n'
        '            "message": "This skill has been imported but not executed. '
        'Use action=\'help\' for instructions.",\n'
        '        }, ensure_ascii=False, indent=2)\n'
        '\n'
        '    return json.dumps({\n'
        '        "ok": False,\n'
        '        "error": f"Unknown action: {action}",\n'
        '    }, ensure_ascii=False)\n'
        '\n'
        '\n'
        'TOOL_DEF = {\n'
        f'    "name": {name_escaped},\n'
        '    "description": (\n'
        f'        "[IMPORTED] {description[:120]} "\n'
        '        "Imported from external skill: '
        f'{source_name}. "\n'
        '        "Use action=\'run\' to execute, '
        '\"describe\" for overview, \"help\" for instructions."\n'
        '    ),\n'
        '    "handler": _handler,\n'
        '    "parameters": {\n'
        '        "type": "object",\n'
        '        "properties": {\n'
        '            "action": {\n'
        '                "type": "string",\n'
        '                "enum": ["run", "describe", "help"],\n'
        '                "description": "What action to perform.",\n'
        '                "default": "run",\n'
        '            },\n'
        '        },\n'
        '        "required": [],\n'
        '    },\n'
        '    "risk_level": "low",\n'
        '}\n'
    )
    return code


def _import_file(path: str) -> dict:
    """Import a single skill file into BAW."""
    p = Path(path)
    if not p.exists():
        return {"status": "failed", "error": f"file not found: {path}"}

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"status": "failed", "error": f"read error: {e}"}

    skill = _parse_hermes_skill(content)
    if not skill["name"]:
        skill["name"] = p.stem

    raw_name = skill["name"].replace("-", "_").replace(" ", "_").lower()
    try:
        name = _sanitize_name(raw_name)
    except ValueError as e:
        return {"status": "failed", "name": raw_name, "error": str(e)}

    tool_path = _TOOLS_DIR / f"{name}.py"

    if tool_path.exists():
        return {"status": "skipped", "name": name, "reason": "tool already exists", "path": str(tool_path)}

    code = _convert_to_tool_code(skill)

    try:
        import ast
        ast.parse(code)
    except SyntaxError as se:
        return {"status": "failed", "name": name, "error": f"syntax: {se}"}

    try:
        tool_path.write_text(code + "\n")
    except Exception as e:
        return {"status": "failed", "name": name, "error": f"write: {e}"}

    try:
        _register_tool(name)
    except Exception as e:
        tool_path.unlink(missing_ok=True)
        return {"status": "failed", "name": name, "error": f"register: {e}"}

    logger.info("Imported skill: %s → %s", name, tool_path)
    return {"status": "imported", "name": name, "path": str(tool_path),
            "description": skill.get("description", "")[:80]}


def _register_tool(name: str):
    """Add import + register to __init__.py."""
    init_path = _TOOLS_DIR / "__init__.py"
    content = init_path.read_text()

    if f", {name})" in content or f", {name}," in content or f"  {name})" in content:
        return

    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if "from . import (" in line:
            stripped = line.rstrip()
            if stripped.endswith(")"):
                new_lines.append(stripped[:-1] + f", {name})")
            elif stripped.endswith(","):
                new_lines.append(stripped)
                new_lines.append(f"               {name},")
            else:
                new_lines.append(stripped + f",\n               {name},")
        else:
            new_lines.append(line)
    content = "\n".join(new_lines)

    lines = content.split("\n")
    last_reg = -1
    for i, line in enumerate(lines):
        if "register(**" in line:
            last_reg = i
    if last_reg >= 0:
        lines.insert(last_reg + 1, f"    register(**{name}.TOOL_DEF)")
        content = "\n".join(lines)

    init_path.write_text(content)


def _handler(
    source: str = "",
    dry_run: bool = False,
) -> str:
    """Import skills from other systems into BAW tools.

    Reads skill files (Hermes SKILL.md format, or any markdown with YAML frontmatter),
    parses them, and converts to BAW-compatible tools.

    Args:
        source: Path to skill file, directory of skills, or 'auto' for auto-detect
        dry_run: If True, just report what would be imported without modifying
    """
    results = {"imported": 0, "skipped": 0, "failed": 0, "items": []}

    files_to_import = []
    if not source or source == "auto":
        candidates = [
            str(Path.home() / ".hermes" / "skills"),
            str(Path.home() / ".hermes" / "profiles" / "sticky" / "skills"),
        ]
        for c in candidates:
            p = Path(c)
            if p.exists():
                for f in p.rglob("SKILL.md"):
                    files_to_import.append(str(f))
                break
    elif Path(source).is_dir():
        for f in Path(source).rglob("SKILL.md"):
            files_to_import.append(str(f))
    else:
        files_to_import = [source]

    # Deduplicate
    files_to_import = list(dict.fromkeys(files_to_import))
    results["found"] = len(files_to_import)

    if dry_run:
        for f in files_to_import:
            p = Path(f)
            try:
                display = str(p.relative_to(Path.home()))
            except ValueError:
                display = p.name
            results["items"].append({
                "file": display,
                "would_import": True,
            })
        return json.dumps(results, ensure_ascii=False, indent=2)

    for f in files_to_import:
        r = _import_file(f)
        results["items"].append(r)
        if r["status"] == "imported":
            results["imported"] += 1
        elif r["status"] == "failed":
            results["failed"] += 1
        else:
            results["skipped"] += 1

    return json.dumps(results, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "skill_import",
    "description": (
        "[COMPATIBILITY] Import skills from other systems (Hermes, etc.) "
        "into BAW-compatible tools. Reads SKILL.md format, parses YAML frontmatter, "
        "converts to Python tools with proper TOOL_DEF and handler. "
        "Auto-detects Hermes skills directory."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Path to skill file, directory, or 'auto' for auto-detect.",
                "default": "auto",
            },
            "dry_run": {
                "type": "boolean",
                "description": "If True, just report what would be imported.",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "medium",
}
