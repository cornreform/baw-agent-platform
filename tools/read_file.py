"""BAW built-in: read file"""

from pathlib import Path

SENSITIVE_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/master.passwd",
    "/etc/hosts", "/etc/hostname", "/proc/",
    "/root/", "/home/*/.ssh/", "/home/*/.bash_history",
    "id_rsa", "id_ed25519", ".aws/credentials", ".env",
]

def _is_sensitive(path: str) -> tuple[bool, str]:
    path_lower = path.lower().strip()
    for sp in SENSITIVE_PATHS:
        if sp.lower() in path_lower:
            return True, f"❌ Blocked — cannot read sensitive path: {sp}"
    return False, ""

def read_file(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read a text file with line numbers."""
    blocked, reason = _is_sensitive(path)
    if blocked:
        return reason
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
    if p.is_dir():
        _items = [child.name + ('/' if child.is_dir() else '') for child in p.iterdir()]
        return f"Directory listing ({len(_items)} items):\n" + "\n".join(sorted(_items))
    if not p.is_file():
        return f"Error: not a file: {path}"
    
    lines = p.read_text(encoding="utf-8").split("\n")
    start = max(0, offset - 1)
    end = start + limit
    selected = lines[start:end]
    
    result = "\n".join(
        f"{i + offset}|{line}" for i, line in enumerate(selected)
    )
    
    total = len(lines)
    result += f"\n--- {len(selected)}/{total} lines ---"
    return result


TOOL_DEF = {
    "name": "read_file",
    "description": "Read a text file with line numbers. Cross-platform.",
    "handler": read_file,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to file (supports ~ expansion)"
            },
            "offset": {
                "type": "integer",
                "description": "Start line (1-indexed, default: 1)",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read (default: 500)",
            },
        },
        "required": ["path"],
    },
    "risk_level": "low",
}
