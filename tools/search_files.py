"""BAW built-in: search_files — grep/codebase search.

Search file contents with regex or find files by glob pattern.
Uses ripgrep (rg) when available for speed, falls back to Python.
"""
import re
import os
import time
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

    r = sp.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode == 1:
        return f"No matches found for '{pattern}'"
    if r.returncode > 1 and r.stderr.strip():
        # Only raise on true errors, not on I/O errors for unreadable files
        _stderr = r.stderr.strip()
        _io_error_count = _stderr.count("Input/output error") + _stderr.count("Permission denied")
        _total_lines = len(_stderr.splitlines())
        # If most errors are I/O, just return partial results
        if _io_error_count >= _total_lines * 0.7 and r.stdout.strip():
            lines = r.stdout.strip().splitlines()[:limit]
            if len(lines) < limit:
                lines.append(f"(I/O errors on {_io_error_count} files — partial results)")
            return "\n".join(lines)
        raise RuntimeError(_stderr)

    lines = r.stdout.strip().splitlines()
    if not lines:
        return f"No matches found for '{pattern}'"
    if len(lines) > limit:
        lines = lines[:limit]
        lines.append(f"... ({len(r.stdout.strip().splitlines()) - limit} more results)")

    return "\n".join(lines)


def _py_search(root: str, pattern: str, file_glob: str, limit: int) -> str:
    """Fallback: search using Python standard library with 15s timeout."""
    import fnmatch
    import signal as _sig
    import threading as _th

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex pattern: {e}"

    results = []
    file_pattern = file_glob or "*"
    _start = time.time()
    _timeout = 15.0

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and common ignores
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", ".venv")]

        for fname in sorted(filenames):
            if not fnmatch.fnmatch(fname, file_pattern):
                continue
            # Skip large files (>10MB)
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(fpath) > 10 * 1024 * 1024:
                    continue
            except OSError:
                continue
            # Check timeout
            if time.time() - _start > _timeout:
                results.append(f"⏱️ Search timed out after {_timeout}s — showing partial results")
                break
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
            except (OSError, UnicodeDecodeError):
                continue
        if time.time() - _start > _timeout:
            break
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
