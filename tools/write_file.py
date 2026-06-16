"""BAW built-in: write file"""

from pathlib import Path


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    p = Path(path).expanduser().resolve()
    # Sandbox: only allow writes within BAW project or user home
    BAW_HOME = Path.home() / "baw"
    try:
        p.relative_to(BAW_HOME)
    except ValueError:
        try:
            p.relative_to(Path.home())
        except ValueError:
            return f"❌ Path outside allowed workspace: {p}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    # Verify write
    written = p.read_text(encoding="utf-8")
    if written != content:
        return f"❌ Write verification failed for {p}"
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
