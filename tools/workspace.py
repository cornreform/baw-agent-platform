"""BAW built-in: workspace state management.

Tracks ongoing development work across turns.
State is saved to ~/.baw/workspace/<project_name>.json

Actions:
- save:   Create/update a workspace
- load:   Load a workspace by name
- list:   List all active workspaces
- delete: Remove a workspace
- clear:  Remove all idle workspaces (>24h)

BAW uses this internally to remember "I'm developing project X,
completed step N of M, created files [a, b, c], next steps [d, e]".
"""

import json
import time
from datetime import datetime
from pathlib import Path

_WORKSPACE_DIR = Path.home() / ".baw" / "workspace"
_MAX_IDLE_HOURS = 24


def _ensure_dir():
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def _path(name: str) -> Path:
    _sanitized = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return _WORKSPACE_DIR / f"{_sanitized}.json"


def workspace_save(name: str, state: dict) -> str:
    """Save or update a workspace state.

    Args:
        name: Project/workspace name.
        state: Dict with keys like 'goal', 'step', 'total_steps', 'files', 'next_steps', 'status'.
    """
    _ensure_dir()
    p = _path(name)
    now = time.time()
    data = {
        "name": name,
        "updated_at": now,
        "created_at": p.stat().st_mtime if p.exists() else now,
        "state": state,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _steps = state.get("steps", "N/A")
    _status = state.get("status", "in_progress")
    return f"✅ Workspace '{name}' saved (step {_steps}, status: {_status})"


def workspace_load(name: str) -> str:
    """Load a workspace state.

    Args:
        name: Project/workspace name.
    """
    _ensure_dir()
    p = _path(name)
    if not p.exists():
        return f"⚠️ No workspace found: '{name}'"
    data = json.loads(p.read_text(encoding="utf-8"))
    state = data.get("state", {})
    _age = (time.time() - data["updated_at"]) / 3600
    _age_str = f"{_age:.1f}h" if _age < 24 else f"{_age / 24:.1f}d"
    lines = [
        f"╔═══ Workspace: {name} ═══╗",
        f"│ Goal: {state.get('goal', 'N/A')[:100]}",
        f"│ Step: {state.get('steps', '?')}",
        f"│ Status: {state.get('status', '?')}",
        f"│ Age: {_age_str}",
    ]
    files = state.get("files", [])
    if files:
        _flist = ", ".join(f[:40] for f in files[-5:])
        lines.append(f"│ Files ({len(files)}): {_flist}")
    next_steps = state.get("next_steps", [])
    if next_steps:
        for ns in next_steps[:3]:
            lines.append(f"│ Next: {ns[:80]}")
    lines.append("╚══════════════════════════╝")
    return "\n".join(lines)


def workspace_list() -> str:
    """List all active workspaces."""
    _ensure_dir()
    workspaces = sorted(_WORKSPACE_DIR.glob("*.json"))
    if not workspaces:
        return "📭 No active workspaces."
    now = time.time()
    lines = ["📋 Active workspaces:"]
    for wp in workspaces:
        try:
            data = json.loads(wp.read_text(encoding="utf-8"))
            name = data.get("name", wp.stem)
            state = data.get("state", {})
            age_h = (now - data["updated_at"]) / 3600
            status = state.get("status", "?")
            step = state.get("steps", "?")
            lines.append(f"  • {name} — step {step}, {status} ({age_h:.1f}h old)")
        except Exception:
            lines.append(f"  • {wp.stem} — (corrupt)")
    return "\n".join(lines)


def workspace_delete(name: str) -> str:
    """Delete a workspace.

    Args:
        name: Project/workspace name.
    """
    p = _path(name)
    if not p.exists():
        return f"⚠️ No workspace found: '{name}'"
    p.unlink()
    return f"🗑️ Workspace '{name}' deleted."


def workspace_clear() -> str:
    """Remove all idle workspaces (>24h)."""
    _ensure_dir()
    now = time.time()
    removed = 0
    for wp in _WORKSPACE_DIR.glob("*.json"):
        try:
            data = json.loads(wp.read_text(encoding="utf-8"))
            age_h = (now - data.get("updated_at", 0)) / 3600
            if age_h > _MAX_IDLE_HOURS:
                wp.unlink()
                removed += 1
        except Exception:
            wp.unlink()
            removed += 1
    return f"🧹 Cleared {removed} idle workspace(s)."


def handler(action: str, name: str = "", state: dict | None = None) -> str:
    """Workspace state management.

    Actions:
      save <name> — save/update workspace (requires state dict)
      load <name> — load workspace state
      list        — list active workspaces
      delete <name> — delete workspace
      clear       — remove idle workspaces
    """
    if state is None:
        state = {}
    if action == "save":
        if not name or not state:
            return "Usage: save <name> with state dict"
        return workspace_save(name, state)
    elif action == "load":
        if not name:
            return "Usage: load <name>"
        return workspace_load(name)
    elif action == "list":
        return workspace_list()
    elif action == "delete":
        if not name:
            return "Usage: delete <name>"
        return workspace_delete(name)
    elif action == "clear":
        return workspace_clear()
    return f"Unknown action: {action}. Use save/load/list/delete/clear."


TOOL_DEF = {
    "name": "workspace",
    "description": (
        "Manage project workspace state across turns. "
        "BAW uses this to remember ongoing development work — "
        "current step, files created, next steps. "
        "Actions: save, load, list, delete, clear."
    ),
    "handler": handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "load", "list", "delete", "clear"],
                "description": "Action to perform",
            },
            "name": {
                "type": "string",
                "description": "Workspace/project name (required for save/load/delete)",
            },
            "state": {
                "type": "object",
                "description": "Workspace state dict with keys: goal, steps, total_steps, files, next_steps, status (required for save)",
                "properties": {
                    "goal": {"type": "string"},
                    "steps": {"type": "string"},
                    "total_steps": {"type": "integer"},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["in_progress", "completed", "blocked", "paused"]},
                },
            },
        },
        "required": ["action"],
    },
}
