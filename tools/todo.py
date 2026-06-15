"""BAW built-in: todo — task / thought / follow-up management.

Backed by core.todo_state.TodoState so the list survives process restarts
and follows the user across sessions. Three item types:

  - task     Standard checklist item
  - thought  BAW's self-reflection; never auto-completes
  - followup Action for a future turn/session; surfaced at next boot

The TOOL_DEF interface is a strict superset of the previous in-memory version,
so existing callers keep working. New actions: 'add_thought', 'add_followup',
'surface', 'stats'.
"""
import json
import os
import sys
from pathlib import Path

# Re-use the persistent store. tools/__init__ already adds the repo root to sys.path.
from core.todo_state import TodoState, get_state

# Use a per-process default session. If a real session id is set on the env
# (set by core/loop.py or the CLI), honour it; otherwise "default".
_SESSION_ID = os.environ.get("BAW_SESSION_ID", "default")
_state: TodoState | None = None


def _get_state() -> TodoState:
    global _state
    if _state is None or _state.session_id != _SESSION_ID:
        _state = TodoState(session_id=_SESSION_ID)
    return _state


def set_session(session_id: str) -> None:
    """Reset the bound state when the session id changes (called by loop boot)."""
    global _state, _SESSION_ID
    _SESSION_ID = session_id
    _state = TodoState(session_id=session_id)


# ── STATUS TAGS (no emoji — cross-platform consistent) ────────

_TASK_TAGS = {
    "pending":     "[ ]",
    "in_progress": "[>]",
    "completed":   "[OK]",
    "cancelled":   "[X]",
}
_THOUGHT_TAGS = {
    "pending":     "[THOUGHT]",
    "in_progress": "[THOUGHT]",
    "completed":   "[THOUGHT]",  # thoughts are never "done" — they're captured
    "cancelled":   "[X]",
}
_FOLLOWUP_TAGS = {
    "pending":     "[TODO]",
    "in_progress": "[TODO]",
    "completed":   "[OK]",
    "cancelled":   "[X]",
}
_TYPE_LABEL = {"task": "task", "thought": "thought", "followup": "follow-up"}


# ── Formatter ────────────────────────────────────────────────

def _format_items(items: list, stats: dict | None = None) -> str:
    if not items and not stats:
        return "[OK] No items."
    lines = []
    for it in items:
        if it.type == "thought":
            tag = _THOUGHT_TAGS.get(it.status, "[THOUGHT]")
        elif it.type == "followup":
            tag = _FOLLOWUP_TAGS.get(it.status, "[TODO]")
        else:
            tag = _TASK_TAGS.get(it.status, "[?]")
        type_tag = "" if it.type == "task" else f" [{_TYPE_LABEL[it.type]}]"
        note = f" -- {it.note}" if it.note else ""
        lines.append(f"{tag} [{it.id[-6:]}]{type_tag} {it.content}{note}")

    if stats:
        s = stats
        lines.append("")
        lines.append(
            f"[STATS] task {s['task']['pending']}p/{s['task']['in_progress']}i/{s['task']['completed']}c "
            f"| [THOUGHT] thought {s['thought']['pending']} "
            f"| [TODO] followup {s['followup']['pending']}p/{s['followup']['in_progress']}i"
        )
    return "\n".join(lines)


# ── Handlers ─────────────────────────────────────────────────

def todo_write(todos_json: str, merge: bool = False) -> str:
    """Replace/merge the task portion of the list.

    Kept for back-compat with old callers. Use add_thought/add_followup for the
    new types.
    """
    try:
        new_items = json.loads(todos_json)
    except json.JSONDecodeError as e:
        return f"[FAIL] invalid JSON: {e}"
    if not isinstance(new_items, list):
        return "[FAIL] todos_json must be a JSON array"

    st = _get_state()
    if merge:
        for itm in new_items:
            if not all(k in itm for k in ("id", "content", "status")):
                return f"[FAIL] each item must have id, content, status. Got: {itm}"
            if itm["status"] not in ("pending", "in_progress", "completed", "cancelled"):
                return f"[FAIL] invalid status '{itm['status']}'"
            st.update(itm["id"], **{k: v for k, v in itm.items() if k != "id"})
    else:
        for itm in new_items:
            if not all(k in itm for k in ("id", "content", "status")):
                return f"[FAIL] each item must have id, content, status. Got: {itm}"
            existing = st.get(itm["id"])
            if existing:
                st.update(itm["id"], content=itm["content"], status=itm["status"])
            else:
                st.add(content=itm["content"], type="task")
    return _format_items(st.list(), st.stats())


def todo_read(active_only: bool = False) -> str:
    st = _get_state()
    return _format_items(st.list(active_only=active_only), st.stats())


def todo_add_thought(content: str, note: str = "") -> str:
    """Capture a self-reflection / idea. Always visible until explicitly removed."""
    st = _get_state()
    it = st.add(content=content, type="thought", note=note)
    return f"[THOUGHT] captured [{it.id[-6:]}]: {content}"


def todo_add_followup(content: str, note: str = "") -> str:
    """Schedule an action for a future turn/session. Will surface at next boot."""
    st = _get_state()
    it = st.add(content=content, type="followup", note=note)
    return f"[TODO] follow-up scheduled [{it.id[-6:]}]: {content}"


def todo_surface() -> str:
    """List pending follow-ups from THIS session AND carried over from previous ones."""
    st = _get_state()
    local = st.list(active_only=True)
    carried = st.load_pending_followups()
    if not local and not carried:
        return "[OK] No pending items -- clean slate."
    out = []
    if carried:
        out.append("[TODO] **Carried over from previous sessions:**")
        for it in carried:
            tag = f" (from {it.session_id})" if it.session_id else ""
            out.append(f"  [TODO] [{it.id[-6:]}]{tag} {it.content}")
    if local:
        if out:
            out.append("")
        out.append("[PLAN] **This session:**")
        out.append(_format_items(local, st.stats()))
    return "\n".join(out)


def todo_stats() -> str:
    st = _get_state()
    s = st.stats()
    return _format_items([], s)


def todo_done(item_id: str) -> str:
    st = _get_state()
    full_id = _resolve_id(st, item_id)
    if not full_id:
        return f"[FAIL] id '{item_id}' not found"
    it = st.complete(full_id)
    if not it:
        return f"[FAIL] id '{item_id}' not found"
    return f"[OK] done [{it.id[-6:]}]: {it.content}"


def todo_cancel(item_id: str) -> str:
    st = _get_state()
    full_id = _resolve_id(st, item_id)
    if not full_id:
        return f"[FAIL] id '{item_id}' not found"
    it = st.cancel(full_id)
    if not it:
        return f"[FAIL] id '{item_id}' not found"
    return f"[X] cancelled [{it.id[-6:]}]: {it.content}"


def todo_remove(item_id: str) -> str:
    st = _get_state()
    full_id = _resolve_id(st, item_id)
    if not full_id:
        return f"[FAIL] id '{item_id}' not found"
    if st.remove(full_id):
        return f"[DEL] removed [{full_id[-6:]}]"
    return "[FAIL] remove failed"


def _resolve_id(st: TodoState, short_or_full: str) -> str | None:
    """Accept either a full id or the last-6-chars suffix."""
    for it in st.list():
        if it.id == short_or_full or it.id.endswith(short_or_full):
            return it.id
    return None


# ── Dispatcher ───────────────────────────────────────────────

def _todo_dispatcher(action: str, **kwargs) -> str:
    if action == "write":
        return todo_write(todos_json=kwargs.get("todos_json", "[]"),
                          merge=kwargs.get("merge", False))
    if action == "read":
        return todo_read(active_only=kwargs.get("active_only", False))
    if action == "add_thought":
        return todo_add_thought(content=kwargs["content"], note=kwargs.get("note", ""))
    if action == "add_followup":
        return todo_add_followup(content=kwargs["content"], note=kwargs.get("note", ""))
    if action == "surface":
        return todo_surface()
    if action == "stats":
        return todo_stats()
    if action == "done":
        return todo_done(item_id=kwargs["item_id"])
    if action == "cancel":
        return todo_cancel(item_id=kwargs["item_id"])
    if action == "remove":
        return todo_remove(item_id=kwargs["item_id"])
    return (f"[FAIL] unknown action '{action}'. "
            f"Use: write, read, add_thought, add_followup, surface, stats, done, cancel, remove")


TOOL_DEF = {
    "name": "todo",
    "description": (
        "Persistent task/thought/followup list across sessions.\n"
        "Types: task (pending/in_progress/done/cancelled), thought (self-reflection, never auto-done), "
        "followup (surfaced at next boot).\n"
        "Actions: write(todos, merge), read(active_only), add_thought(content, note), "
        "add_followup(content, note), surface, stats, done/cancel/remove(item_id).\n"
        "Use for any multi-step task, plan, or self-review."
    ),
    "handler": _todo_dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "read", "add_thought", "add_followup",
                         "surface", "stats", "done", "cancel", "remove"],
            },
            "todos_json": {"type": "string"},
            "merge": {"type": "boolean", "default": False},
            "active_only": {"type": "boolean", "default": False},
            "content": {"type": "string"},
            "note": {"type": "string", "default": ""},
            "item_id": {"type": "string"},
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
