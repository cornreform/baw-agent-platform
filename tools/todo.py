"""BAW built-in: todo — task list management.

Simple in-memory task list (per session). Tracks pending/in-progress/completed/cancelled.
"""
import json


# In-memory store (per process lifetime)
_todos: list[dict] = []


def todo_write(todos_json: str, merge: bool = False) -> str:
    """Write or update the task list.

    Args:
        todos_json: JSON array of tasks: [{"id":"1","content":"...","status":"pending"},...]
        merge: If true, update existing items by id and add new ones. If false, replace entire list.

    Returns:
        Formatted task list summary.
    """
    global _todos

    try:
        new_items = json.loads(todos_json)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e}"

    if not isinstance(new_items, list):
        return "Error: todos_json must be a JSON array"

    # Validate items
    for item in new_items:
        if "id" not in item or "content" not in item or "status" not in item:
            return f"Error: each item must have 'id', 'content', 'status'. Got: {item}"
        if item["status"] not in ("pending", "in_progress", "completed", "cancelled"):
            return f"Error: invalid status '{item['status']}'. Use: pending, in_progress, completed, cancelled"

    if merge and _todos:
        # Update existing, add new
        existing_ids = {t["id"]: t for t in _todos}
        for item in new_items:
            if item["id"] in existing_ids:
                existing_ids[item["id"]].update(item)
            else:
                _todos.append(item)
    else:
        _todos = new_items

    return _format_todos()


def todo_read() -> str:
    """Read the current task list."""
    return _format_todos()


def _format_todos() -> str:
    """Format the current todo list."""
    if not _todos:
        return "✅ No tasks."

    status_icons = {
        "pending": "⬜",
        "in_progress": "🔄",
        "completed": "✅",
        "cancelled": "❌",
    }

    lines = []
    for t in _todos:
        icon = status_icons.get(t["status"], "❓")
        lines.append(f"{icon} [{t['id']}] {t['content']}")

    # Stats
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
    for t in _todos:
        s = t.get("status", "")
        if s in counts:
            counts[s] += 1

    stats = " | ".join(f"{icon} {counts[s]}" for s, icon in status_icons.items())
    lines.append(f"\n📊 {stats} | **total: {len(_todos)}**")

    return "\n".join(lines)


def _todo_dispatcher(action: str, todos_json: str = "[]", merge: bool = False) -> str:
    """Dispatch todo actions."""
    if action == "write":
        return todo_write(todos_json=todos_json, merge=merge)
    elif action == "read":
        return todo_read()
    else:
        return f"Error: unknown action '{action}'. Use 'write' or 'read'."


TOOL_DEF = {
    "name": "todo",
    "description": (
        "Manage a task list for the current session. "
        "Use action='write' with todos_json to set/create tasks. "
        "Use action='read' to view current tasks. "
        "Each task: {id, content, status} where status is pending|in_progress|completed|cancelled. "
        "merge=true updates existing tasks by id instead of replacing all."
    ),
    "handler": _todo_dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "read"],
                "description": "'write' to set/update tasks, 'read' to view current list.",
            },
            "todos_json": {
                "type": "string",
                "description": "For 'write': JSON array of task objects. Each: {id, content, status}.",
            },
            "merge": {
                "type": "boolean",
                "description": "For 'write': if true, update existing by id instead of replacing.",
                "default": False,
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
