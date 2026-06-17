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
import os
import re
import sys
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_TOOLS_DIR = _BAW_HOME / "tools"


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

    # Extract YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if fm_match:
        frontmatter = fm_match.group(1)
        result["body"] = fm_match.group(2).strip()

        # Parse key-value pairs
        for line in frontmatter.split("\n"):
            line = line.strip()
            if ":" in line:
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
                    result["tags"] = [t.strip().strip('"').strip("[]") for t in val.split(",")]
                elif key in ("parameters", "params"):
                    result["parameters"]["raw"] = val
    else:
        # No frontmatter — treat whole content as body
        result["body"] = content.strip()

    # Auto-detect name from body if not in frontmatter
    if not result["name"]:
        # Look for # Name or similar
        h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1:
            result["name"] = h1.group(1).strip().lower().replace(" ", "-")

    return result


def _convert_to_tool_code(skill: dict) -> str:
    """Convert parsed skill data into BAW tool Python code."""
    name = skill.get("name", "").replace("-", "_").replace(" ", "_") or "imported_skill"
    description = skill.get("description", f"Imported from skill: {name}")
    body = skill.get("body", "")
    tags = skill.get("tags", [])

    # Extract example commands/usage from body
    examples = re.findall(r"```(?:bash|shell)?\n(.*?)```", body, re.DOTALL)

    code = f'''"""BAW built-in: {name} — {description}

Imported from external skill.
"""
import json
import os
import subprocess
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))


def _handler(
    action: str = "run",
) -> str:
    """Execute this skill/task.

    Imported from: {skill.get("name", "external source")}
    Original description: {description}

    Args:
        action: What to do ("run", "describe", "help")
    """
    if action == "describe":
        return json.dumps({{
            "name": "{name}",
            "description": \"\"\"{description}\"\"\",
            "tags": {json.dumps(tags)},
            "body_preview": \"\"\"{body[:200]}\"\"\",
        }}, ensure_ascii=False, indent=2)

    if action == "help":
        return json.dumps({{
            "instructions": \"\"\"{body[:500]}\"\"\",
            "examples": {json.dumps(examples[:3])},
        }}, ensure_ascii=False, indent=2)

    if action == "run":
        return json.dumps({{
            "ok": True,
            "message": "This skill has been imported but not executed. Use action='help' for instructions.",
        }}, ensure_ascii=False, indent=2)

    return json.dumps({{
        "ok": False,
        "error": f"Unknown action: {{action}}",
    }}, ensure_ascii=False)


TOOL_DEF = {{
    "name": "{name}",
    "description": (
        "[IMPORTED] {description[:120]} "
        "Imported from external skill: {skill.get('name', 'unknown')}. "
        "Use action='run' to execute, 'describe' for overview, 'help' for instructions."
    ),
    "handler": _handler,
    "parameters": {{
        "type": "object",
        "properties": {{
            "action": {{
                "type": "string",
                "enum": ["run", "describe", "help"],
                "description": "What action to perform.",
                "default": "run",
            }},
        }},
        "required": [],
    }},
    "risk_level": "low",
}}
'''
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

    # Parse
    skill = _parse_hermes_skill(content)
    if not skill["name"]:
        skill["name"] = p.stem

    name = skill["name"].replace("-", "_").replace(" ", "_").lower()
    tool_path = _TOOLS_DIR / f"{name}.py"

    # Check if already exists
    if tool_path.exists():
        return {"status": "skipped", "name": name, "reason": "tool already exists", "path": str(tool_path)}

    # Convert
    code = _convert_to_tool_code(skill)

    # Syntax check
    try:
        import ast
        ast.parse(code)
    except SyntaxError as se:
        return {"status": "failed", "name": name, "error": f"syntax: {se}"}

    # Write
    try:
        tool_path.write_text(code + "\n")
    except Exception as e:
        return {"status": "failed", "name": name, "error": f"write: {e}"}

    # Register
    try:
        _register_tool(name)
    except Exception as e:
        tool_path.unlink(missing_ok=True)
        return {"status": "failed", "name": name, "error": f"register: {e}"}

    return {"status": "imported", "name": name, "path": str(tool_path),
            "description": skill.get("description", "")[:80]}


def _register_tool(name: str):
    """Add import + register to __init__.py."""
    init_path = _TOOLS_DIR / "__init__.py"
    content = init_path.read_text()

    # Add to import line
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if "from . import (" in line and "bash" in line:
            stripped = line.rstrip()
            if stripped.endswith(")"):
                new_lines.append(stripped[:-1] + f", {name})")
            elif stripped.endswith(","):
                new_lines.append(stripped)
                new_lines.append(f"               {name},")
            else:
                new_lines.append(stripped + f",\n               {name}")
        else:
            new_lines.append(line)
    content = "\n".join(new_lines)

    # Add register line after last register
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

    # Resolve source
    files_to_import = []
    if not source or source == "auto":
        # Auto-detect: check Hermes skills dir
        candidates = [
            str(Path.home() / ".hermes" / "skills"),
            str(Path.home() / ".hermes" / "profiles" / "sticky" / "skills"),
        ]
        for c in candidates:
            p = Path(c)
            if p.exists():
                for f in p.rglob("SKILL.md"):
                    files_to_import.append(str(f))
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix == ".md":
                        files_to_import.append(str(f))
                break
    elif Path(source).is_dir():
        for f in Path(source).rglob("SKILL.md"):
            files_to_import.append(str(f))
        for f in Path(source).rglob("*.md"):
            if "skill" in f.name.lower() or "readme" in f.name.lower():
                files_to_import.append(str(f))
    else:
        files_to_import = [source]

    results["found"] = len(files_to_import)

    if dry_run:
        for f in files_to_import:
            p = Path(f)
            results["items"].append({
                "file": str(p.relative_to(Path.home()) if p.is_relative_to(Path.home()) else p.name),
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
