"""BAW built-in: memory — persistent memory read/write/search.

Interface to BAW's MemoryStore (JSONL + graph edges).
"""
import sys
from pathlib import Path


def _load_store():
    """Lazy-load MemoryStore (avoid circular import)."""
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)
    from core.memory import MemoryStore
    data_dir = Path.home() / ".baw"
    return MemoryStore(data_dir)


def memory_remember(content: str, tags: str = "", source: str = "agent") -> str:
    """Save a memory entry. Routes through curation gate for judgment.

    The curator decides: save new, update existing, merge, or discard.
    This prevents noise, ensures corrections update old entries, and
    assigns priority scores based on content value.

    Args:
        content: The text content to remember.
        tags: Comma-separated tags for categorization (e.g., 'bug,fix,python').
        source: Who created this memory ('user', 'agent', 'system').

    Returns:
        Confirmation with memory ID and curation decision.
    """
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from core.memory import MemoryStore
    from core.memory_curator import curate

    data_dir = Path.home() / ".baw"
    mem = MemoryStore(data_dir)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # ── Load recent entries for conflict detection ──
    _recent = list(reversed(mem._cache))[:100]

    # ── Run curation gate ──
    decision = curate(
        content=content,
        tags=tag_list,
        source=source,
        existing_entries=_recent,
    )

    # ── Act on curator decision ──
    if decision["action"] == "discard":
        return f"[>] 消噪: {decision['reason']}"

    if decision["action"] == "update":
        # Update existing entry in cache
        target_id = decision["target_id"]
        new_score = decision["score"]
        for entry in mem._cache:
            if entry["id"] == target_id:
                entry["content"] = decision["content"]
                entry["score"] = min(1.0, max(new_score, entry.get("score", 0.5)))
                entry["tags"] = list(set(entry.get("tags", []) + decision.get("tags", [])))
                entry["access_count"] += 1
                from datetime import datetime, timezone
                entry["last_accessed"] = datetime.now(timezone.utc).isoformat()
                mem._save_all()
                return (
                    f"[OK] 記憶已修正 (id: {target_id}) — {decision['reason']}"
                )
        # Fallback: if target not found, save as new
        entry = mem.remember(content=content, tags=tag_list, source=source)
        return f"[OK] 記憶已保存 (id: {entry.get('id', '?')}) — {decision['reason']} (target not found, saved as new)"

    if decision["action"] == "flag":
        # Save flagged entry with note — system can review later
        flagged = f"[FLAGGED] {content}"
        entry = mem.remember(content=flagged, tags=tag_list + ["flagged"], source=source)
        return (
            f"[!] 記憶已標記 (id: {entry.get('id', '?')}) — {decision['reason']}\n"
            f"    系統將在下次 curation 週期複查此條目。"
        )

    # ── save (default): save with curator-assigned score ──
    entry = mem.remember(content=content, tags=tag_list, source=source)

    # Override initial score with curator's value judgment
    # Keep score higher if curator thinks it's more valuable
    if entry.get("id") != "rejected":
        curator_score = decision.get("score", 0.5)
        for e in mem._cache:
            if e["id"] == entry["id"]:
                # Only boost — never lower initial score
                if curator_score > e.get("score", 0.5):
                    e["score"] = min(1.0, curator_score)
                    mem._save_all()
                break

    if entry.get("id") == "rejected":
        return f"[>] 已過濾: {decision['reason']}"
    return f"[OK] 記憶已保存 (id: {entry.get('id', '?')}) — {decision['reason']}"


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
        "Each write is filtered through an intelligent curation gate that: "
        "(a) classifies content value, (b) checks for conflicts with existing memories, "
        "(c) updates old entries when new info corrects them, (d) discards noise. "
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
