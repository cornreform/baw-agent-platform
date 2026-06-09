"""BAW built-in: search_files — grep/codebase search.

Search file contents with regex or find files by glob pattern.
Uses ripgrep (rg) when available for speed, falls back to Python.
"""
import re
import os
import subprocess as sp
from pathlib import Path


def search_files(
    pattern: str,
    path: str = ".",
    file_glob: str = "",
    limit: int = 50,
) -> str:
    """Search file contents or find files by name.

    Args:
        pattern: Regex pattern for content search, or glob for file search.
        path: Directory to search in (default: current working directory).
        file_glob: Filter to specific file patterns (e.g., '*.py').
        limit: Max results to return (default: 50).

    Returns:
        Formatted search results with file paths and matching lines.
    """
    search_path = Path(path).expanduser().resolve()
    if not search_path.exists():
        return f"Error: path not found: {search_path}"

    # Try ripgrep first
    try:
        return _rg_search(str(search_path), pattern, file_glob, limit)
    except (FileNotFoundError, sp.TimeoutExpired):
        pass

    # Fallback to Python
    return _py_search(str(search_path), pattern, file_glob, limit)


def _rg_search(root: str, pattern: str, file_glob: str, limit: int) -> str:
    """Search using ripgrep."""
    cmd = ["rg", "--line-number", "--no-heading", "--color=never", "-e", pattern]
    if file_glob:
        cmd.extend(["--glob", file_glob])
    cmd.append(root)

    r = sp.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode == 1:
        return f"No matches found for '{pattern}'"
    if r.returncode > 1:
        raise RuntimeError(r.stderr.strip())

    lines = r.stdout.strip().splitlines()
    if not lines:
        return f"No matches found for '{pattern}'"
    if len(lines) > limit:
        lines = lines[:limit]
        lines.append(f"... ({len(r.stdout.strip().splitlines()) - limit} more results)")

    return "\n".join(lines)


def _py_search(root: str, pattern: str, file_glob: str, limit: int) -> str:
    """Fallback: search using Python standard library."""
    import fnmatch

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex pattern: {e}"

    results = []
    file_pattern = file_glob or "*"

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and common ignores
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]

        for fname in sorted(filenames):
            if not fnmatch.fnmatch(fname, file_pattern):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(fpath, root)
                            results.append(f"{rel}:{i}:{line.rstrip()}")
                            if len(results) >= limit:
                                break
                if len(results) >= limit:
                    break
            except (OSError, PermissionError):
                continue
        if len(results) >= limit:
            break

    if not results:
        return f"No matches found for '{pattern}'"

    return "\n".join(results)


TOOL_DEF = {
    "name": "search_files",
    "description": (
        "Search file contents with regex or find files by name. "
        "Use this instead of grep/rg/find in bash — it's faster and respects .gitignore. "
        "Returns file paths with matching line numbers."
    ),
    "handler": search_files,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for inside files",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: current working directory)",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Filter files by pattern (e.g., '*.py', '*.md')",
                "default": "",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 50)",
                "default": 50,
            },
        },
        "required": ["pattern"],
    },
    "risk_level": "low",
}
