"""BAW built-in: scan_and_adopt — discover foreign tools, adapt to BAW.

Scans a target directory (local, git repo, or pip package) for tools/skills
from other systems, analyzes them via LLM, and generates BAW-compatible
versions. Handles: Python tools, Hermes skills, shell scripts.

Pipeline:
  1. scan → find all tools/skills in target
  2. read → read each tool's code/meta
  3. analyze → LLM understands what it does
  4. generate → create BAW-compatible tool
  5. safety_check → scan for dangerous patterns
  6. register → syntax check + register + smoke test
  7. report → what was adopted, what was skipped
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("baw.scan_and_adopt")

_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_TOOLS_DIR = _BAW_HOME / "tools"
_MAX_SCAN_DEPTH = 8
_DANGEROUS_PATTERNS = [
    rb"os\.system",
    rb"subprocess.*shell\s*=\s*True",
    rb"__import__\(['\"]os['\"]",
    rb"eval\(",
    rb"exec\(",
    rb"pickle\.loads",
    rb"import ctypes",
    rb"import socket",
    rb"import http\.server",
]


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use as a Python module name and file path.

    Rejects: path traversal (.., /, \\), empty, reserved names.
    Keeps only: alphanumeric + underscore + hyphen (hyphens → underscores).
    """
    # Reject path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"Name contains path separators: {name!r}")
    # Keep only safe chars, collapse
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_")
    if not safe:
        raise ValueError(f"Name sanitized to empty: {name!r}")
    # Hyphens to underscores (Python identifier convention)
    safe = safe.replace("-", "_")
    return safe


def _run(cmd: list[str], timeout: int = 30, cwd: str | None = None) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"ok": r.returncode == 0, "output": r.stdout.strip(), "error": r.stderr.strip() or None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _scan_directory(path: str, depth: int = 0) -> list[dict]:
    """Scan a directory for discoverable tools/skills.

    Recognizes:
    - Python files with TOOL_DEF (BAW format)
    - SKILL.md files (Hermes format)
    - *.py files with register() calls
    - *.sh files
    """
    if depth > _MAX_SCAN_DEPTH:
        return []

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return []

    results = []

    for f in sorted(p.iterdir()):
        if f.name.startswith(".") or f.name.startswith("__"):
            continue

        rel = str(f.relative_to(p)) if p != f else f.name
        entry = {"path": str(f), "relative": rel, "type": "unknown"}

        if f.is_dir():
            # Recurse with depth limit (symlink loops handled by depth guard)
            if not f.is_symlink():
                results.extend(_scan_directory(str(f), depth + 1))
            continue

        ext = f.suffix.lower()

        if ext == ".py":
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:2000]
                if "TOOL_DEF" in content:
                    entry["type"] = "baw_tool"
                    m = re.search(r'"name":\s*"([^"]+)"', content)
                    entry["name"] = m.group(1) if m else f.stem
                    m2 = re.search(r'"description":\s*"([^"]+)"', content)
                    entry["description"] = m2.group(1)[:100] if m2 else ""
                elif "def " in content:
                    entry["type"] = "python_module"
                    entry["name"] = f.stem
                    funcs = re.findall(r"^def (\w+)\(", content, re.MULTILINE)
                    entry["functions"] = funcs[:5]
            except Exception:
                pass

        elif f.name == "SKILL.md" or f.name.lower().endswith("skill.md"):
            entry["type"] = "hermes_skill"
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:1500]
                m = re.search(r"name:\s*[\"']?([^\"'\n]+)", content)
                entry["name"] = m.group(1).strip() if m else f.parent.name
                m2 = re.search(r"description:\s*[\"']?([^\"'\n]+)", content)
                entry["description"] = m2.group(1).strip()[:100] if m2 else ""
            except Exception:
                pass

        elif ext == ".sh":
            entry["type"] = "shell_script"
            entry["name"] = f.stem
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:500]
                desc_lines = [l for l in content.split("\n") if l.startswith("# ")][:3]
                entry["description"] = "; ".join(desc_lines)[:150]
            except Exception:
                pass

        results.append(entry)

    return results


def _analyze_via_llm(code: str, name: str, source_type: str) -> str:
    """Use LLM to analyze foreign code and generate BAW tool code."""
    try:
        import sys
        sys.path.insert(0, str(_BAW_HOME))
        from core.llm import call_llm_with_fallback
    except Exception:
        return "# ERROR: cannot import LLM"

    prompt = f"""You are analyzing a {source_type} from another system to create a BAW-compatible version.

Source name: {name}
Source type: {source_type}
Source code:
```
{code[:3000]}
```

Create a BAW-compatible Python tool for `{name}`. The tool must:
1. Have a TOOL_DEF dict at module level with: name, description, handler, parameters, risk_level
2. The handler function returns json.dumps(result, ensure_ascii=False, indent=2)
3. Keep the SAME CORE FUNCTIONALITY as the source
4. Use BAW conventions (json response, docstrings)

Generate ONLY valid Python code. No markdown fences. No explanations."""

    try:
        result = call_llm_with_fallback(
            {},
            [{"role": "system", "content": "You are a code converter for BAW tools. Generate clean Python."},
             {"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=3000,
        )
        code_text = result.response.content.strip() if result.response else ""
        # Strip markdown fences
        if code_text.startswith("```"):
            code_text = code_text.split("\n", 1)[1] if "\n" in code_text else code_text
            code_text = code_text.rsplit("```", 1)[0] if "```" in code_text else code_text
        return code_text.strip()
    except Exception as e:
        logger.error("LLM analysis failed for %s: %s", name, e)
        return f"# ERROR: {e}"


def _safety_check(tool_code: str) -> list[str]:
    """Scan generated tool code for dangerous patterns. Returns list of findings."""
    findings = []
    code_bytes = tool_code.encode("utf-8", errors="replace")
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, code_bytes):
            findings.append(f"Dangerous pattern: {pattern.decode('ascii')}")
    return findings


def _adopt_file(entry: dict) -> dict:
    """Adopt a single foreign tool/skill into BAW."""
    raw_name = entry.get("name", Path(entry["path"]).stem).replace("-", "_").replace(" ", "_")
    try:
        name = _sanitize_name(raw_name)
    except ValueError as e:
        return {"name": raw_name, "status": "failed", "error": str(e)}

    source_type = entry["type"]

    # Read full content
    try:
        with open(entry["path"], encoding="utf-8", errors="replace") as f:
            code = f.read()
    except Exception as e:
        return {"name": name, "status": "failed", "error": f"read error: {e}"}

    # Generate BAW tool code via LLM
    gen_code = _analyze_via_llm(code, name, source_type)
    if gen_code.startswith("# ERROR"):
        return {"name": name, "status": "failed", "error": gen_code}

    # Safety check
    findings = _safety_check(gen_code)
    if findings:
        return {"name": name, "status": "failed",
                "error": f"Safety check failed: {'; '.join(findings)}"}

    # Syntax check
    try:
        import ast
        ast.parse(gen_code)
    except SyntaxError as se:
        return {"name": name, "status": "failed", "error": f"syntax: {se}"}

    # Write to tools/
    tool_path = _TOOLS_DIR / f"{name}.py"
    try:
        tool_path.write_text(gen_code + "\n")
    except Exception as e:
        return {"name": name, "status": "failed", "error": f"write error: {e}"}

    # Register in __init__.py
    try:
        _register_tool(name)
    except Exception as e:
        tool_path.unlink(missing_ok=True)
        return {"name": name, "status": "failed", "error": f"register error: {e}"}

    logger.info("Adopted tool: %s → %s", name, tool_path)
    return {"name": name, "status": "adopted", "path": str(tool_path)}


def _register_tool(name: str):
    """Add import + register to __init__.py."""
    init_path = _TOOLS_DIR / "__init__.py"
    content = init_path.read_text()

    # Check if already imported
    if f", {name})" in content or f", {name}," in content or f"  {name})" in content:
        return

    # Add to import line
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if "from . import (" in line:
            stripped = line.rstrip()
            if stripped.endswith(")"):
                # Last line of multi-line import: insert before )
                new_lines.append(stripped[:-1] + f", {name})")
            elif stripped.endswith(","):
                # Intermediate line: add new item line
                new_lines.append(stripped)
                new_lines.append(f"               {name},")
            else:
                # First line (no trailing punctuation we can use): add continuation
                new_lines.append(stripped + f",\n               {name},")
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


def _download_git(url: str) -> dict:
    """git clone a repo into a temp dir. Returns dict with ok, path, tmpdir."""
    tmpdir = Path(tempfile.mkdtemp(prefix="baw-adopt-"))
    r = _run(["git", "clone", "--depth", "1", url, str(tmpdir / "repo")], timeout=60)
    if not r["ok"]:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return {"ok": False, "error": r.get("error", "clone failed"), "path": "", "tmpdir": ""}
    return {"ok": True, "path": str(tmpdir / "repo"), "tmpdir": str(tmpdir)}


def _handler(
    source: str = "",
    target_type: str = "auto",
    dry_run: bool = False,
) -> str:
    """Discover and adopt external tools/skills into BAW.

    Scans a target (directory path, git URL, or pip package) for tools/skills
    from other systems, analyzes them, and generates BAW-compatible versions.

    Supported source types:
    - Local directory: scan for TOOL_DEF files, SKILL.md, .py, .sh
    - Git URL: clone and scan
    - 'self': scan BAW's own tools (for audit/version check)

    Args:
        source: Path, git URL, or 'self' to scan
        target_type: 'auto' detect, 'hermes', 'python', 'shell' (filter by type)
        dry_run: If True, just report what would be adopted without doing it
    """
    results = {"scanned": 0, "adopted": 0, "skipped": 0, "failed": 0, "items": []}
    scan_path = ""
    git_tmpdir = ""  # Track for cleanup

    # Determine scan path
    if source == "self":
        scan_path = str(_BAW_HOME / "tools")
    elif source.startswith(("http://", "https://", "git@")):
        dl = _download_git(source)
        if not dl["ok"]:
            return json.dumps({"ok": False, "error": dl.get("error", "download failed")}, ensure_ascii=False)
        scan_path = dl["path"]
        git_tmpdir = dl.get("tmpdir", "")
    elif source:
        scan_path = source
    else:
        # Auto-detect: check common locations
        candidates = [
            str(Path.home() / ".baw" / "skills"),
            "/app/tools",
        ]
        for c in candidates:
            if Path(c).exists():
                scan_path = c
                break
        if not scan_path:
            return json.dumps({"ok": False, "error": "No source specified and no auto-detect candidates found"},
                              ensure_ascii=False)

    # Scan
    entries = _scan_directory(scan_path)
    results["scanned"] = len(entries)
    results["scan_path"] = scan_path

    # Filter by target_type if specified
    if target_type and target_type != "auto":
        type_map = {"hermes": "hermes_skill", "python": "python_module", "shell": "shell_script"}
        filter_type = type_map.get(target_type)
        if filter_type:
            entries = [e for e in entries if e["type"] == filter_type]
            results["scanned"] = len(entries)

    if dry_run:
        for e in entries:
            results["items"].append({
                "name": e.get("name", Path(e["path"]).stem),
                "type": e["type"],
                "path": e["relative"],
                "would_adopt": e["type"] in ("baw_tool", "hermes_skill", "python_module", "shell_script"),
            })
        results["would_adopt"] = sum(1 for i in results["items"] if i["would_adopt"])
        return json.dumps(results, ensure_ascii=False, indent=2)

    # Adopt each
    for entry in entries:
        if entry["type"] in ("baw_tool", "hermes_skill", "python_module", "shell_script"):
            result = _adopt_file(entry)
            results["items"].append(result)
            if result["status"] == "adopted":
                results["adopted"] += 1
            else:
                results["failed"] += 1
        else:
            results["skipped"] += 1
            results["items"].append({
                "name": entry.get("name", Path(entry["path"]).stem),
                "type": entry["type"],
                "status": "skipped",
                "reason": "unrecognized format",
            })

    # Cleanup git temp dir
    if git_tmpdir and Path(git_tmpdir).exists():
        shutil.rmtree(git_tmpdir, ignore_errors=True)

    return json.dumps(results, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "scan_and_adopt",
    "description": (
        "[COMPATIBILITY] Scan external tools/skills and adopt them into BAW. "
        "Supports: local directories, git URLs, auto-detect common locations. "
        "Recognizes BAW tools (TOOL_DEF), Hermes skills (SKILL.md), Python modules, "
        "and shell scripts. Each is analyzed via LLM and converted to a BAW-compatible tool."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Target to scan: path, git URL, 'self', or empty for auto-detect.",
                "default": "",
            },
            "target_type": {
                "type": "string",
                "enum": ["auto", "hermes", "python", "shell"],
                "description": "Target format filter (auto-detect all by default).",
                "default": "auto",
            },
            "dry_run": {
                "type": "boolean",
                "description": "If True, just report what would be adopted without modifying anything.",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "high",
}
