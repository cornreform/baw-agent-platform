"""BAW built-in: list_files — directory listing with mtime sorting.

Lists files in a directory sorted by modification time (newest first by default).
Returns filename, size, and human-readable modification time.
"""
import os
import math
from datetime import datetime, timezone
from pathlib import Path


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.log(size_bytes, 1024)) if size_bytes > 0 else 0
    i = min(i, len(units) - 1)
    return f"{size_bytes / (1024 ** i):.1f} {units[i]}"


def _format_time(ts: float) -> str:
    """Format epoch timestamp to human-readable string."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def list_files(
    path: str = ".",
    pattern: str = "*",
    sort_by: str = "mtime",
    reverse: bool = True,
    limit: int = 50,
    include_dirs: bool = False,
) -> str:
    """List files in a directory with metadata.

    Args:
        path: Directory to list (default: current directory).
        pattern: Glob pattern to filter files (e.g., '*.jpg', 'photo_*').
        sort_by: Sort key — 'mtime' (modification time), 'name', or 'size'.
        reverse: If True, newest/largest first. If False, oldest/smallest first.
        limit: Max files to return (default: 50).
        include_dirs: Include directories in results (default: False).

    Returns:
        Formatted file listing with mtime, size, and name.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: path not found: {path}"
    if not p.is_dir():
        return f"Error: not a directory: {path}"

    entries = list(p.iterdir())

    if not include_dirs:
        entries = [e for e in entries if e.is_file()]

    # Filter by glob pattern
    import fnmatch
    if pattern != "*":
        entries = [e for e in entries if fnmatch.fnmatch(e.name, pattern)]

    # Gather stats
    files = []
    for entry in entries:
        try:
            st = entry.stat()
            files.append({
                "name": entry.name,
                "path": str(entry),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_str": _format_time(st.st_mtime),
                "size_str": _format_size(st.st_size),
            })
        except OSError:
            continue

    if not files:
        return f"No files found in {p}{' matching ' + pattern if pattern != '*' else ''}"

    # Sort
    if sort_by == "mtime":
        files.sort(key=lambda f: f["mtime"], reverse=reverse)
    elif sort_by == "size":
        files.sort(key=lambda f: f["size"], reverse=reverse)
    elif sort_by == "name":
        files.sort(key=lambda f: f["name"].lower(), reverse=reverse)
    else:
        return f"Error: invalid sort_by '{sort_by}'. Use 'mtime', 'name', or 'size'."

    # Limit
    total = len(files)
    files = files[:limit]

    # Format output
    lines = [f"📁 {p}/ ({total} files, showing {len(files)})"]
    sort_label = {"mtime": "newest first", "name": "name", "size": "largest first"}
    if reverse:
        lines.append(f"Sorted by: {sort_label.get(sort_by, sort_by)}")
    else:
        lines.append(f"Sorted by: {sort_by} (ascending)")

    for f in files:
        lines.append(f"  {f['mtime_str']}  {f['size_str']:>8}  {f['name']}")

    if total > limit:
        lines.append(f"  ... ({total - limit} more files)")

    return "\n".join(lines)


TOOL_DEF = {
    "name": "list_files",
    "description": (
        "List files in a directory sorted by modification time (newest first). "
        "Use this to find recently created/modified files, e.g. latest photos, "
        "downloads, or logs. Supports filtering by glob pattern and sorting by "
        "name or size."
    ),
    "handler": list_files,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list (default: current directory)",
                "default": ".",
            },
            "pattern": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g., '*.jpg', 'photo_*')",
                "default": "*",
            },
            "sort_by": {
                "type": "string",
                "description": "Sort by 'mtime' (modification time), 'name', or 'size'",
                "default": "mtime",
            },
            "reverse": {
                "type": "boolean",
                "description": "True = newest/largest first, False = oldest/smallest first",
                "default": True,
            },
            "limit": {
                "type": "integer",
                "description": "Max files to return (default: 50)",
                "default": 50,
            },
            "include_dirs": {
                "type": "boolean",
                "description": "Include directories in results",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "low",
}
