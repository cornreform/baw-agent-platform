"""BAW built-in: memory — persistent memory read/write/search.

Interface to BAW's MemoryStore (JSONL + graph edges).
"""
import sys
from pathlib import Path


def memory_remember(content: str, tags: str = "", source: str = "agent") -> str:
    """Save a memory entry.

    Args:
        content: The text content to remember.
        tags: Comma-separated tags for categorization (e.g., 'bug,fix,python').
        source: Who created this memory ('user', 'agent', 'system').

    Returns:
        Confirmation with memory ID.
    """
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from core.memory import MemoryStore

    data_dir = Path.home() / ".baw"
    mem = MemoryStore(data_dir)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    entry = mem.remember(content=content, tags=tag_list, source=source)
    return f"✅ Memory saved (id: {entry.get('id', '?')}, tags: {entry.get('tags', [])})"


def memory_search(query: str, limit: int = 10) -> str:
    """Search memory for relevant entries.

    Args:
        query: Search query string.
        limit: Max results to return (default: 10).

    Returns:
        Formatted list of matching memories.
    """
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from core.memory import MemoryStore

    data_dir = Path.home() / ".baw"
    mem = MemoryStore(data_dir)
    results = mem.search(query=query, limit=limit)

    if not results:
        return f"No memories found for '{query}'"

    lines = []
    for r in results:
        tags = ", ".join(r.get("tags", []))
        content = (r.get("content", "") or "")[:200]
        lines.append(f"- [{r.get('id', '?')}] {content}")
        if tags:
            lines[-1] += f"  `#{tags}`"
    return "\n".join(lines)


def memory_recall(limit: int = 20) -> str:
    """Recall recent memories (most recent first).

    Args:
        limit: Max entries to return (default: 20).

    Returns:
        Formatted list of recent memories.
    """
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from core.memory import MemoryStore

    data_dir = Path.home() / ".baw"
    mem = MemoryStore(data_dir)

    entries = list(reversed(mem._cache))[:limit]
    if not entries:
        return "No memories stored yet."

    lines = []
    for e in entries:
        tags = ", ".join(e.get("tags", []))
        content = (e.get("content", "") or "")[:200]
        lines.append(f"- [{e.get('id', '?')}] {content}")
        if tags:
            lines[-1] += f"  `#{tags}`"
    return "\n".join(lines)


def _memory_dispatcher(action: str, content: str = "", tags: str = "", limit: int = 10) -> str:
    """Dispatch memory actions."""
    if action == "remember":
        if not content:
            return "Error: 'content' is required for 'remember' action"
        return memory_remember(content=content, tags=tags)
    elif action == "search":
        if not content:
            return "Error: 'content' (query) is required for 'search' action"
        return memory_search(query=content, limit=limit)
    elif action == "recall":
        return memory_recall(limit=limit or 20)
    else:
        return f"Error: unknown action '{action}'. Use 'remember', 'search', or 'recall'."


TOOL_DEF = {
    "name": "memory",
    "description": (
        "Manage persistent memories — save facts, preferences, and lessons learned. "
        "Use 'action=remember' to save a new memory. "
        "Use 'action=search' to find memories by query. "
        "Use 'action=recall' to list recent memories."
    ),
    "handler": _memory_dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "search", "recall"],
                "description": "What to do: 'remember' saves, 'search' finds, 'recall' lists recent.",
            },
            "content": {
                "type": "string",
                "description": "For 'remember': the text to save. For 'search': the query string.",
            },
            "tags": {
                "type": "string",
                "description": "For 'remember': comma-separated tags (e.g., 'bug,fix,python')",
            },
            "limit": {
                "type": "integer",
                "description": "For 'search'/'recall': max results (default: 10 for search, 20 for recall)",
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
