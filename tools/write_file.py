"""BAW built-in: write file — auto-backup before self-modification."""

from pathlib import Path


# ── Detect BAW paths from environment ────────────────────────
_BAW_HOME = Path("/app")  # container default
_BAW_DATA = Path.home() / ".baw"

# Try to detect runtime env
_home_env = __import__("os").environ.get("BAW_HOME", "")
if _home_env:
    _BAW_HOME = Path(_home_env)
_data_env = __import__("os").environ.get("BAW_RUNTIME_HOME", "")
if _data_env:
    _BAW_DATA = Path(_data_env)


def _should_auto_backup(path: Path) -> bool:
    """Check if path is within BAW's own code or data dirs."""
    try:
        path.relative_to(_BAW_HOME)
        return True
    except ValueError:
        pass
    try:
        path.relative_to(_BAW_DATA)
        return True
    except ValueError:
        pass
    return False


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    p = Path(path).expanduser().resolve()

    # ── Auto-backup before self-modification ──
    if _should_auto_backup(p):
        try:
            from core.backup import auto_pre_mod_backup
            bkp = auto_pre_mod_backup()
        except Exception:
            pass  # non-fatal — proceed with write even if backup fails

    # Sandbox: only allow writes within BAW project or user home
    try:
        p.relative_to(_BAW_HOME)
    except ValueError:
        try:
            p.relative_to(Path.home())
        except ValueError:
            return f"Error: Path outside allowed workspace: {p}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    # Verify write
    written = p.read_text(encoding="utf-8")
    if written != content:
        return f"Error: Write verification failed for {p}"
    return f"Written {len(content)} bytes to {p}"


TOOL_DEF = {
    "name": "write_file",
    "description": "Write content to a file. Creates directories if needed. Cross-platform. Auto-backups before modifying BAW's own files.",
    "handler": write_file,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to write (supports ~ expansion)",
            },
            "content": {
                "type": "string",
                "description": "Content to write",
            },
        },
        "required": ["path", "content"],
    },
    "risk_level": "medium",
}
