"""BAW built-in: read file"""

from pathlib import Path


def read_file(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read a text file with line numbers."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {path}"
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
