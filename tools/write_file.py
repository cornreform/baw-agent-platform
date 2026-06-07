"""BAW built-in: write file"""

from pathlib import Path


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {p}"


TOOL_DEF = {
    "name": "write_file",
    "description": "Write content to a file. Creates directories if needed. Cross-platform.",
    "handler": write_file,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to write (supports ~ expansion)"
            },
            "content": {
                "type": "string",
                "description": "Content to write"
            },
        },
        "required": ["path", "content"],
    },
    "risk_level": "medium",
}
